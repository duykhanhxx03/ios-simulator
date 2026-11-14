//! Mach-style VM calls on Windows, over placeholders.
//!
//! reims-vgpu's core speaks Mach VM (`mach_vm_allocate`, `mach_vm_remap` as a shared alias,
//! `mach_vm_protect`, `mach_vm_deallocate`). Linux's platform layer emulates them with
//! `mmap(MAP_SHARED | MAP_FIXED)` of the files behind the guest's RAM. Windows can do the same
//! with *placeholders* (Windows 10 1803+):
//!
//! - an address range is reserved as a placeholder (`VirtualAlloc2`, `MEM_RESERVE_PLACEHOLDER`);
//! - a placeholder is split or coalesced (`VirtualFree` with `MEM_PRESERVE_PLACEHOLDER` /
//!   `MEM_COALESCE_PLACEHOLDERS`) and replaced by private memory (`VirtualAlloc2`,
//!   `MEM_REPLACE_PLACEHOLDER`) or by a view of a section (`MapViewOfFile3`,
//!   `MEM_REPLACE_PLACEHOLDER`), at page (4 KiB) granularity, not the 64 KiB one plain views take;
//! - a view or a private allocation goes back to a placeholder (`UnmapViewOfFile2` / `VirtualFree`
//!   with `MEM_PRESERVE_PLACEHOLDER`).
//!
//! Every range this crate hands out lives in a [`Region`] it tracks, cut into [`Piece`]s, each one
//! OS allocation (a placeholder, a private allocation or one view). A piece of a view knows its
//! section and offset, which is what makes a *shared alias* (Mach's remap) possible: the alias is
//! another view of the same section pages. Private memory cannot be aliased, as Linux's anonymous
//! memory cannot: such a source answers [`Kern::INVALID_ADDRESS`]. Ranges that must be aliasable
//! come from [`allocate_aliasable`] (a section of their own).
//!
//! Verified on Windows 11 / RTX 5060 by `vphone-win/spikes/hostvm-import`: views at 16 KiB and
//! 4 KiB pieces alias correctly and a multi-view range imports into Vulkan by host pointer.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};

#[cfg(windows)]
mod sys;

/// A Mach-style return code (`kern_return_t`): only [`Kern::SUCCESS`] is success.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Kern(pub i32);

impl Kern {
    pub const SUCCESS: Kern = Kern(0);
    pub const INVALID_ADDRESS: Kern = Kern(1);
    pub const PROTECTION_FAILURE: Kern = Kern(2);
    pub const NO_SPACE: Kern = Kern(3);
    pub const INVALID_ARGUMENT: Kern = Kern(4);
    pub const FAILURE: Kern = Kern(5);
}

pub type Result<T> = std::result::Result<T, Kern>;

/// Page protection of a piece.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Prot {
    None,
    Read,
    ReadWrite,
}

impl Prot {
    /// Mach's `vm_prot_t` bits (1 read, 2 write): write implies read here, as on x86-64 pages.
    pub fn from_mach(bits: i32) -> Prot {
        match bits & 3 {
            0 => Prot::None,
            1 => Prot::Read,
            _ => Prot::ReadWrite,
        }
    }
}

/// A pagefile-backed section: the file a view's pages belong to (Linux's memfd).
pub struct Section {
    #[cfg(windows)]
    handle: sys::Handle,
    len: u64,
}

impl std::fmt::Debug for Section {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Section").field("len", &self.len).finish()
    }
}

impl Section {
    /// A fresh zero-filled section of `len` bytes (committed against the pagefile).
    pub fn new(len: u64) -> std::io::Result<Arc<Section>> {
        #[cfg(windows)]
        {
            let handle = sys::create_section(len)?;
            Ok(Arc::new(Section { handle, len }))
        }
        #[cfg(not(windows))]
        {
            let _ = len;
            Err(std::io::Error::from(std::io::ErrorKind::Unsupported))
        }
    }

    /// A section handed over by another component (QEMU's guest RAM), owned from now on.
    ///
    /// # Safety
    /// `handle` must be a section handle with read/write access, valid for `len` bytes, that
    /// nothing else closes.
    #[cfg(windows)]
    pub unsafe fn from_raw(handle: *mut std::ffi::c_void, len: u64) -> Arc<Section> {
        Arc::new(Section { handle: sys::Handle(handle), len })
    }

    pub fn len(&self) -> u64 {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// The raw handle (to duplicate into another process, or for the caller's own views).
    #[cfg(windows)]
    pub fn raw(&self) -> *mut std::ffi::c_void {
        self.handle.0
    }
}

/// What occupies one piece of a region.
#[derive(Debug, Clone)]
enum Kind {
    /// A placeholder: reserved, no access (Mach: deallocated).
    Reserved,
    /// Private zero-filled memory: cannot be aliased.
    Private,
    /// A view of `section` from byte `offset`.
    View { section: Arc<Section>, offset: u64 },
}

/// One OS allocation inside a region.
#[derive(Debug, Clone)]
struct Piece {
    len: u64,
    kind: Kind,
    prot: Prot,
}

/// A reserved address range, cut into pieces that tile it.
#[derive(Debug)]
struct Region {
    len: u64,
    pieces: BTreeMap<u64, Piece>,
}

static REGIONS: Mutex<BTreeMap<u64, Region>> = Mutex::new(BTreeMap::new());

fn regions() -> MutexGuard<'static, BTreeMap<u64, Region>> {
    REGIONS.lock().unwrap_or_else(|e| e.into_inner())
}

/// The host page size (4 KiB on x86-64 Windows).
pub fn page_size() -> u64 {
    static PAGE: OnceLock<u64> = OnceLock::new();
    *PAGE.get_or_init(|| {
        #[cfg(windows)]
        {
            sys::page_size()
        }
        #[cfg(not(windows))]
        {
            4096
        }
    })
}

fn aligned(v: u64) -> bool {
    v % page_size() == 0
}

/// The region holding `[at, at + len)` entirely: its base.
fn region_of(map: &BTreeMap<u64, Region>, at: u64, len: u64) -> Result<u64> {
    let (&base, region) = map.range(..=at).next_back().ok_or(Kern::INVALID_ADDRESS)?;
    let end = at.checked_add(len).ok_or(Kern::INVALID_ARGUMENT)?;
    if end > base + region.len {
        return Err(Kern::INVALID_ADDRESS);
    }
    Ok(base)
}

/// Reserve `len` bytes of address space as one placeholder: no access until something is mapped.
pub fn reserve(len: u64) -> Result<u64> {
    if len == 0 || !aligned(len) {
        return Err(Kern::INVALID_ARGUMENT);
    }
    #[cfg(windows)]
    {
        let base = sys::reserve_placeholder(len).map_err(|_| Kern::NO_SPACE)?;
        let mut pieces = BTreeMap::new();
        pieces.insert(base, Piece { len, kind: Kind::Reserved, prot: Prot::None });
        regions().insert(base, Region { len, pieces });
        Ok(base)
    }
    #[cfg(not(windows))]
    Err(Kern::FAILURE)
}

/// `mach_vm_allocate(ANYWHERE)`: fresh zero-filled read/write private memory (not aliasable).
pub fn allocate(len: u64) -> Result<u64> {
    let base = reserve(len)?;
    if let Err(e) = overwrite(base, len, Kind::Private, Prot::ReadWrite) {
        let _ = deallocate(base, len);
        return Err(e);
    }
    Ok(base)
}

/// A fresh zero-filled read/write range that aliases can be made of: a view of a section of its
/// own (Linux: a shared mapping of a memfd).
pub fn allocate_aliasable(len: u64) -> Result<u64> {
    let section = Section::new(len).map_err(|_| Kern::NO_SPACE)?;
    let base = reserve(len)?;
    if let Err(e) = overwrite(base, len, Kind::View { section, offset: 0 }, Prot::ReadWrite) {
        let _ = deallocate(base, len);
        return Err(e);
    }
    Ok(base)
}

/// `mach_vm_allocate(FIXED | OVERWRITE)` inside a range this crate handed out: zero fill.
pub fn allocate_fixed_overwrite(at: u64, len: u64) -> Result<()> {
    overwrite(at, len, Kind::Private, Prot::ReadWrite)
}

/// A view of `section` from `offset` mapped over `[at, at + len)` (Linux: `mmap(MAP_FIXED)` of a
/// file), replacing what was there.
pub fn map_section(at: u64, section: &Arc<Section>, offset: u64, len: u64, prot: Prot) -> Result<()> {
    if offset.checked_add(len).is_none_or(|e| e > section.len) || !aligned(offset) {
        return Err(Kern::INVALID_ARGUMENT);
    }
    overwrite(at, len, Kind::View { section: Arc::clone(section), offset }, prot)
}

/// What backs `[at, at + len)`: one `(section, offset, length)` run per view piece, in order.
/// Fails with [`Kern::INVALID_ADDRESS`] where a byte is private or unmapped (nothing to alias).
pub fn backing(at: u64, len: u64) -> Result<Vec<(Arc<Section>, u64, u64)>> {
    if len == 0 || !aligned(at) || !aligned(len) {
        return Err(Kern::INVALID_ARGUMENT);
    }
    if let Some(run) = foreign_backing(at, len) {
        return Ok(vec![run]);
    }
    let map = regions();
    let base = region_of(&map, at, len)?;
    let region = &map[&base];
    let end = at + len;
    let mut runs: Vec<(Arc<Section>, u64, u64)> = Vec::new();
    let first = region.pieces.range(..=at).next_back().map(|(&s, _)| s).unwrap_or(base);
    for (&start, piece) in region.pieces.range(first..end) {
        let lo = start.max(at);
        let hi = (start + piece.len).min(end);
        if lo >= hi {
            continue;
        }
        let Kind::View { section, offset } = &piece.kind else {
            return Err(Kern::INVALID_ADDRESS);
        };
        let off = offset + (lo - start);
        // Runs that follow on in one section merge, as Linux's VMAs do.
        if let Some(last) = runs.last_mut() {
            if Arc::ptr_eq(&last.0, section) && last.1 + last.2 == off {
                last.2 += hi - lo;
                continue;
            }
        }
        runs.push((Arc::clone(section), off, hi - lo));
    }
    Ok(runs)
}

/// Ranges another component mapped itself (QEMU's guest RAM: its own view of a section it made),
/// registered so aliases of their pages can be made: looked up by [`backing`], never overwritten.
static FOREIGN: Mutex<BTreeMap<u64, (u64, Arc<Section>, u64)>> = Mutex::new(BTreeMap::new());

/// Registers `[host, host + len)` as a view of `section` from `offset` mapped by someone else.
/// Returns a guard that unregisters it when dropped.
pub fn register_foreign(host: u64, len: u64, section: Arc<Section>, offset: u64) -> Result<ForeignRange> {
    if len == 0 || !aligned(host) || !aligned(len) || !aligned(offset) {
        return Err(Kern::INVALID_ARGUMENT);
    }
    if offset.checked_add(len).is_none_or(|e| e > section.len) {
        return Err(Kern::INVALID_ARGUMENT);
    }
    let mut map = FOREIGN.lock().unwrap_or_else(|e| e.into_inner());
    let overlaps = map
        .range(..host + len)
        .next_back()
        .is_some_and(|(&s, &(l, _, _))| s + l > host);
    if overlaps {
        return Err(Kern::INVALID_ARGUMENT);
    }
    map.insert(host, (len, section, offset));
    Ok(ForeignRange { host })
}

/// A registered foreign range; unregistered on drop.
#[derive(Debug)]
pub struct ForeignRange {
    host: u64,
}

impl Drop for ForeignRange {
    fn drop(&mut self) {
        FOREIGN.lock().unwrap_or_else(|e| e.into_inner()).remove(&self.host);
    }
}

/// The backing of `[at, at + len)` inside one foreign range, if it lies in one.
fn foreign_backing(at: u64, len: u64) -> Option<(Arc<Section>, u64, u64)> {
    let map = FOREIGN.lock().unwrap_or_else(|e| e.into_inner());
    let (&start, (flen, section, offset)) = map.range(..=at).next_back()?;
    (at + len <= start + flen).then(|| (Arc::clone(section), offset + (at - start), len))
}

/// `mach_vm_remap(OVERWRITE, copy = FALSE)`: a shared alias of `[src, src + len)` at `dst`.
/// All or nothing: the source must be all views (aliasable); otherwise nothing changes.
pub fn remap_shared_overwrite(dst: u64, src: u64, len: u64) -> Result<u64> {
    let runs = backing(src, len)?;
    let mut at = dst;
    for (section, offset, run) in runs {
        overwrite(at, run, Kind::View { section, offset }, Prot::ReadWrite)?;
        at += run;
    }
    Ok(dst)
}

/// `mach_vm_protect(set_maximum = TRUE)`: the protection of every page of the range.
pub fn protect(at: u64, len: u64, prot: Prot) -> Result<()> {
    if len == 0 {
        return Ok(());
    }
    if !aligned(at) || !aligned(len) {
        return Err(Kern::INVALID_ARGUMENT);
    }
    let mut map = regions();
    let base = region_of(&map, at, len)?;
    let region = map.get_mut(&base).expect("region");
    split_at(region, at)?;
    split_at(region, at + len)?;
    for (&start, piece) in region.pieces.range_mut(at..at + len) {
        if matches!(piece.kind, Kind::Reserved) {
            return Err(Kern::PROTECTION_FAILURE);
        }
        #[cfg(windows)]
        sys::protect(start, piece.len, prot).map_err(|_| Kern::PROTECTION_FAILURE)?;
        #[cfg(not(windows))]
        let _ = start;
        piece.prot = prot;
    }
    Ok(())
}

/// `mach_vm_deallocate`: the range goes back to reserved (no access); a region that is all
/// reserved gives its address space back.
pub fn deallocate(at: u64, len: u64) -> Kern {
    if len == 0 {
        return Kern::SUCCESS;
    }
    if let Err(e) = overwrite(at, len, Kind::Reserved, Prot::None) {
        return e;
    }
    let mut map = regions();
    let Ok(base) = region_of(&map, at, len) else {
        return Kern::SUCCESS;
    };
    let region = &map[&base];
    if region.pieces.values().all(|p| matches!(p.kind, Kind::Reserved)) {
        #[cfg(windows)]
        {
            if region.pieces.len() > 1 {
                let _ = sys::coalesce(base, region.len);
            }
            let _ = sys::release(base);
        }
        map.remove(&base);
    }
    Kern::SUCCESS
}

/// Replace `[at, at + len)` with one piece of `kind`.
fn overwrite(at: u64, len: u64, kind: Kind, prot: Prot) -> Result<()> {
    if len == 0 || !aligned(at) || !aligned(len) {
        return Err(Kern::INVALID_ARGUMENT);
    }
    let mut map = regions();
    let base = region_of(&map, at, len)?;
    let region = map.get_mut(&base).expect("region");
    split_at(region, at)?;
    split_at(region, at + len)?;
    let inside: Vec<u64> = region.pieces.range(at..at + len).map(|(&s, _)| s).collect();
    for &start in &inside {
        let piece = region.pieces.get(&start).expect("piece").clone();
        to_placeholder(start, &piece)?;
        region.pieces.insert(start, Piece { kind: Kind::Reserved, prot: Prot::None, ..piece });
    }
    if inside.len() > 1 {
        #[cfg(windows)]
        sys::coalesce(at, len).map_err(|_| Kern::FAILURE)?;
        for &start in &inside {
            region.pieces.remove(&start);
        }
    }
    let piece = Piece { len, kind, prot };
    place(at, &piece)?;
    region.pieces.insert(at, piece);
    Ok(())
}

/// Make `at` a piece boundary of `region` (a no-op where it already is one or is its end).
fn split_at(region: &mut Region, at: u64) -> Result<()> {
    let Some((&start, piece)) = region.pieces.range(..at).next_back() else {
        return Ok(());
    };
    let end = start + piece.len;
    if at >= end {
        return Ok(());
    }
    let piece = piece.clone();
    let (left_len, right_len) = (at - start, end - at);
    // Private bytes survive the split: copied out, the allocation remade as two, copied back.
    #[cfg(windows)]
    let saved = match piece.kind {
        Kind::Private => Some(sys::save(start, piece.len, piece.prot)),
        _ => None,
    };
    to_placeholder(start, &piece)?;
    #[cfg(windows)]
    sys::split(start, left_len).map_err(|_| Kern::FAILURE)?;
    let (left_kind, right_kind) = match &piece.kind {
        Kind::View { section, offset } => (
            Kind::View { section: Arc::clone(section), offset: *offset },
            Kind::View { section: Arc::clone(section), offset: offset + left_len },
        ),
        k => (k.clone(), k.clone()),
    };
    let left = Piece { len: left_len, kind: left_kind, prot: piece.prot };
    let right = Piece { len: right_len, kind: right_kind, prot: piece.prot };
    place(start, &left)?;
    place(at, &right)?;
    #[cfg(windows)]
    if let Some(bytes) = saved {
        sys::restore(start, &bytes, piece.prot);
    }
    region.pieces.insert(start, left);
    region.pieces.insert(at, right);
    Ok(())
}

/// Turn one piece back into a placeholder of its own extent.
fn to_placeholder(start: u64, piece: &Piece) -> Result<()> {
    #[cfg(windows)]
    match piece.kind {
        Kind::Reserved => {}
        Kind::Private => sys::free_to_placeholder(start, piece.len).map_err(|_| Kern::FAILURE)?,
        Kind::View { .. } => sys::unmap_to_placeholder(start).map_err(|_| Kern::FAILURE)?,
    }
    #[cfg(not(windows))]
    let _ = (start, piece);
    Ok(())
}

/// Fill a placeholder of exactly `piece.len` at `start` with `piece`.
fn place(start: u64, piece: &Piece) -> Result<()> {
    #[cfg(windows)]
    match &piece.kind {
        Kind::Reserved => {}
        Kind::Private => {
            sys::commit_private(start, piece.len).map_err(|_| Kern::NO_SPACE)?;
            if piece.prot != Prot::ReadWrite {
                sys::protect(start, piece.len, piece.prot).map_err(|_| Kern::FAILURE)?;
            }
        }
        Kind::View { section, offset } => {
            sys::map_view(section.handle.0, start, *offset, piece.len, piece.prot)
                .map_err(|_| Kern::NO_SPACE)?;
        }
    }
    #[cfg(not(windows))]
    let _ = (start, piece);
    Ok(())
}

/// Pieces of the region holding `at` (for tests and diagnostics): `(start, len, kind)` where kind
/// is 'R' reserved, 'P' private, 'V' view.
pub fn layout(at: u64) -> Vec<(u64, u64, char)> {
    let map = regions();
    let Ok(base) = region_of(&map, at, 0) else {
        return Vec::new();
    };
    map[&base]
        .pieces
        .iter()
        .map(|(&s, p)| {
            let k = match p.kind {
                Kind::Reserved => 'R',
                Kind::Private => 'P',
                Kind::View { .. } => 'V',
            };
            (s, p.len, k)
        })
        .collect()
}

#[cfg(all(test, windows))]
mod tests;
