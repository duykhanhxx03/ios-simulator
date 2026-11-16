//! The Win32 calls behind the region map.

use std::ffi::c_void;
use std::io;

use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Memory::{
    CreateFileMappingW, MapViewOfFile3, UnmapViewOfFile2, VirtualAlloc2, VirtualFree,
    VirtualProtect, MEMORY_MAPPED_VIEW_ADDRESS,
};
use windows_sys::Win32::System::SystemInformation::{GetSystemInfo, SYSTEM_INFO};
use windows_sys::Win32::System::Threading::GetCurrentProcess;

use crate::Prot;

// The flags, by value (the same numbers serve VirtualFree and UnmapViewOfFile2).
const MEM_COMMIT: u32 = 0x1000;
const MEM_RESERVE: u32 = 0x2000;
const MEM_RELEASE: u32 = 0x8000;
const MEM_RESERVE_PLACEHOLDER: u32 = 0x40000;
const MEM_REPLACE_PLACEHOLDER: u32 = 0x4000;
const MEM_PRESERVE_PLACEHOLDER: u32 = 0x2;
const MEM_COALESCE_PLACEHOLDERS: u32 = 0x1;
const PAGE_NOACCESS: u32 = 0x01;
const PAGE_READONLY: u32 = 0x02;
const PAGE_READWRITE: u32 = 0x04;
const SEC_COMMIT: u32 = 0x0800_0000;

/// An owned kernel handle.
pub(crate) struct Handle(pub(crate) HANDLE);

// SAFETY: a section handle is a process-wide kernel object reference, usable from any thread.
unsafe impl Send for Handle {}
// SAFETY: as above; nothing mutates the handle value.
unsafe impl Sync for Handle {}

impl Drop for Handle {
    fn drop(&mut self) {
        // SAFETY: the handle is owned here alone.
        unsafe { CloseHandle(self.0) };
    }
}

fn check(ok: bool) -> io::Result<()> {
    if ok { Ok(()) } else { Err(io::Error::last_os_error()) }
}

fn page_prot(prot: Prot) -> u32 {
    match prot {
        Prot::None => PAGE_NOACCESS,
        Prot::Read => PAGE_READONLY,
        Prot::ReadWrite => PAGE_READWRITE,
    }
}

pub(crate) fn page_size() -> u64 {
    // SAFETY: a valid out-pointer.
    let mut si: SYSTEM_INFO = unsafe { std::mem::zeroed() };
    unsafe { GetSystemInfo(&mut si) };
    u64::from(si.dwPageSize)
}

pub(crate) fn create_section(len: u64) -> io::Result<Handle> {
    // SAFETY: an anonymous pagefile-backed section; null attributes and name.
    let h = unsafe {
        CreateFileMappingW(
            INVALID_HANDLE_VALUE,
            std::ptr::null(),
            PAGE_READWRITE | SEC_COMMIT,
            (len >> 32) as u32,
            len as u32,
            std::ptr::null(),
        )
    };
    if h.is_null() {
        return Err(io::Error::last_os_error());
    }
    Ok(Handle(h))
}

pub(crate) fn reserve_placeholder(len: u64) -> io::Result<u64> {
    // SAFETY: the kernel picks the address; no extended parameters.
    let p = unsafe {
        VirtualAlloc2(
            GetCurrentProcess(),
            std::ptr::null(),
            len as usize,
            MEM_RESERVE | MEM_RESERVE_PLACEHOLDER,
            PAGE_NOACCESS,
            std::ptr::null_mut(),
            0,
        )
    };
    if p.is_null() {
        return Err(io::Error::last_os_error());
    }
    Ok(p as u64)
}

/// Split the placeholder that starts at `start` so that `[start, start + len)` is one of its own.
pub(crate) fn split(start: u64, len: u64) -> io::Result<()> {
    // SAFETY: a placeholder of this process that the region map owns.
    check(unsafe { VirtualFree(start as *mut c_void, len as usize, MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER) } != 0)
}

/// Coalesce the adjacent placeholders that tile `[start, start + len)` into one.
pub(crate) fn coalesce(start: u64, len: u64) -> io::Result<()> {
    // SAFETY: placeholders of this process that the region map owns.
    check(unsafe { VirtualFree(start as *mut c_void, len as usize, MEM_RELEASE | MEM_COALESCE_PLACEHOLDERS) } != 0)
}

/// Give a whole (single-placeholder) reservation back.
pub(crate) fn release(base: u64) -> io::Result<()> {
    // SAFETY: the region's base, a placeholder this crate reserved.
    check(unsafe { VirtualFree(base as *mut c_void, 0, MEM_RELEASE) } != 0)
}

pub(crate) fn commit_private(start: u64, len: u64) -> io::Result<()> {
    // SAFETY: replaces a placeholder of exactly `len` bytes the region map owns.
    let p = unsafe {
        VirtualAlloc2(
            GetCurrentProcess(),
            start as *const c_void,
            len as usize,
            MEM_RESERVE | MEM_COMMIT | MEM_REPLACE_PLACEHOLDER,
            PAGE_READWRITE,
            std::ptr::null_mut(),
            0,
        )
    };
    if p as u64 != start {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub(crate) fn free_to_placeholder(start: u64, len: u64) -> io::Result<()> {
    // SAFETY: a private allocation of exactly `len` bytes of this process that the region map
    // owns; with MEM_PRESERVE_PLACEHOLDER the size is the allocation's (not 0) and it becomes a
    // placeholder again.
    check(unsafe { VirtualFree(start as *mut c_void, len as usize, MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER) } != 0)
}

pub(crate) fn map_view(section: HANDLE, start: u64, offset: u64, len: u64, prot: Prot) -> io::Result<()> {
    let first = if prot == Prot::ReadWrite { PAGE_READWRITE } else { PAGE_READONLY };
    // SAFETY: replaces a placeholder of exactly `len` bytes the region map owns with a view of a
    // live section.
    let v: MEMORY_MAPPED_VIEW_ADDRESS = unsafe {
        MapViewOfFile3(
            section,
            GetCurrentProcess(),
            start as *const c_void,
            offset,
            len as usize,
            MEM_REPLACE_PLACEHOLDER,
            first,
            std::ptr::null_mut(),
            0,
        )
    };
    if v.Value as u64 != start {
        return Err(io::Error::last_os_error());
    }
    if prot == Prot::None {
        protect(start, len, Prot::None)?;
    }
    Ok(())
}

pub(crate) fn unmap_to_placeholder(start: u64) -> io::Result<()> {
    // SAFETY: a view of this process that the region map owns.
    check(unsafe {
        UnmapViewOfFile2(
            GetCurrentProcess(),
            MEMORY_MAPPED_VIEW_ADDRESS { Value: start as *mut c_void },
            MEM_PRESERVE_PLACEHOLDER,
        )
    } != 0)
}

pub(crate) fn protect(start: u64, len: u64, prot: Prot) -> io::Result<()> {
    let mut old = 0u32;
    // SAFETY: committed pages of this process that the region map owns.
    check(unsafe { VirtualProtect(start as *const c_void, len as usize, page_prot(prot), &mut old) } != 0)
}

/// A private piece's bytes, read even where it is not readable.
pub(crate) fn save(start: u64, len: u64, prot: Prot) -> Vec<u8> {
    if prot == Prot::None {
        let _ = protect(start, len, Prot::Read);
    }
    let mut bytes = vec![0u8; len as usize];
    // SAFETY: `len` committed, readable bytes at `start`.
    unsafe { std::ptr::copy_nonoverlapping(start as *const u8, bytes.as_mut_ptr(), len as usize) };
    bytes
}

/// Write saved bytes back over fresh private pieces, then put the protection back.
pub(crate) fn restore(start: u64, bytes: &[u8], prot: Prot) {
    let len = bytes.len() as u64;
    let _ = protect(start, len, Prot::ReadWrite);
    // SAFETY: `len` committed, writable bytes at `start` (the two pieces just made).
    unsafe { std::ptr::copy_nonoverlapping(bytes.as_ptr(), start as *mut u8, bytes.len()) };
    if prot != Prot::ReadWrite {
        let _ = protect(start, len, prot);
    }
}
