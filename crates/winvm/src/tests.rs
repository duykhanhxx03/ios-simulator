use super::*;

const PAGE: u64 = 4096;

fn fill(at: u64, len: u64, byte: u8) {
    // SAFETY: test-owned writable pages.
    unsafe { std::ptr::write_bytes(at as *mut u8, byte, len as usize) };
}

fn byte(at: u64) -> u8 {
    // SAFETY: test-owned readable page.
    unsafe { std::ptr::read_volatile(at as *const u8) }
}

#[test]
fn allocate_is_zeroed_and_writable() {
    let a = allocate(4 * PAGE).unwrap();
    assert!((0..4 * PAGE).step_by(512).all(|o| byte(a + o) == 0));
    fill(a, 4 * PAGE, 0x5a);
    assert_eq!(byte(a + 4 * PAGE - 1), 0x5a);
    assert_eq!(deallocate(a, 4 * PAGE), Kern::SUCCESS);
}

#[test]
fn private_memory_cannot_be_aliased() {
    let a = allocate(2 * PAGE).unwrap();
    let b = allocate(2 * PAGE).unwrap();
    assert_eq!(remap_shared_overwrite(b, a, PAGE), Err(Kern::INVALID_ADDRESS));
    deallocate(a, 2 * PAGE);
    deallocate(b, 2 * PAGE);
}

#[test]
fn remap_makes_a_shared_alias_both_ways() {
    let src = allocate_aliasable(4 * PAGE).unwrap();
    for i in 0..4 {
        fill(src + i * PAGE, PAGE, 0x10 + i as u8);
    }
    let dst = allocate(4 * PAGE).unwrap();
    fill(dst, 4 * PAGE, 0xb0);
    // Alias source page 2 at destination page 1: the rest of the destination keeps its bytes.
    assert_eq!(remap_shared_overwrite(dst + PAGE, src + 2 * PAGE, PAGE), Ok(dst + PAGE));
    assert_eq!([byte(dst), byte(dst + PAGE), byte(dst + 2 * PAGE), byte(dst + 3 * PAGE)], [0xb0, 0x12, 0xb0, 0xb0]);
    fill(dst + PAGE + 100, 1, 0xee);
    assert_eq!(byte(src + 2 * PAGE + 100), 0xee, "a write through the alias shows in the source");
    fill(src + 2 * PAGE + 200, 1, 0xdd);
    assert_eq!(byte(dst + PAGE + 200), 0xdd, "and the other way");
    assert_eq!(layout(dst).iter().map(|p| p.2).collect::<String>(), "PVP");
    deallocate(src, 4 * PAGE);
    // The alias keeps showing the pages after the source range goes, as Mach's does.
    assert_eq!(byte(dst + PAGE), 0x12);
    deallocate(dst, 4 * PAGE);
}

#[test]
fn a_view_of_a_view_maps_the_section() {
    let src = allocate_aliasable(2 * PAGE).unwrap();
    fill(src, 2 * PAGE, 0x33);
    let v1 = reserve(2 * PAGE).unwrap();
    remap_shared_overwrite(v1, src, 2 * PAGE).unwrap();
    let v2 = reserve(2 * PAGE).unwrap();
    remap_shared_overwrite(v2, v1, 2 * PAGE).unwrap();
    fill(v2 + 7, 1, 0x44);
    assert_eq!(byte(src + 7), 0x44);
    for r in [src, v1, v2] {
        deallocate(r, 2 * PAGE);
    }
}

#[test]
fn scattered_pages_in_any_order_like_a_guest_buffer() {
    // 64 guest pages of 16 KiB, viewed backwards in 4 KiB pieces (an x86 macOS guest's pattern).
    let guest = allocate_aliasable(64 * 4 * PAGE).unwrap();
    for p in 0..64 * 4 {
        fill(guest + p * PAGE, PAGE, p as u8);
    }
    let n = 256u64;
    let view = reserve(n * PAGE).unwrap();
    let t = std::time::Instant::now();
    for i in 0..n {
        remap_shared_overwrite(view + i * PAGE, guest + (n - 1 - i) * PAGE, PAGE).unwrap();
    }
    let per = t.elapsed() / n as u32;
    for i in 0..n {
        assert_eq!(byte(view + i * PAGE), (n - 1 - i) as u8);
    }
    let runs = backing(view, n * PAGE).unwrap();
    assert_eq!(runs.len(), n as usize, "backwards pages never merge");
    eprintln!("remap of one 4 KiB page: {per:?}");
    deallocate(view, n * PAGE);
    deallocate(guest, 64 * 4 * PAGE);
}

#[test]
fn forward_runs_merge_in_backing() {
    let src = allocate_aliasable(8 * PAGE).unwrap();
    let v = reserve(8 * PAGE).unwrap();
    for i in 0..8 {
        remap_shared_overwrite(v + i * PAGE, src + i * PAGE, PAGE).unwrap();
    }
    let runs = backing(v, 8 * PAGE).unwrap();
    assert_eq!(runs.len(), 1);
    assert_eq!((runs[0].1, runs[0].2), (0, 8 * PAGE));
    deallocate(v, 8 * PAGE);
    deallocate(src, 8 * PAGE);
}

#[test]
fn fixed_overwrite_zeroes_the_middle_and_keeps_the_edges() {
    let a = allocate(4 * PAGE).unwrap();
    for i in 0..4 {
        fill(a + i * PAGE, PAGE, 0xa0 + i as u8);
    }
    allocate_fixed_overwrite(a + PAGE, 2 * PAGE).unwrap();
    assert_eq!([byte(a), byte(a + PAGE), byte(a + 2 * PAGE), byte(a + 3 * PAGE)], [0xa0, 0, 0, 0xa3]);
    // The same over views: their edges still alias their section.
    let s = allocate_aliasable(4 * PAGE).unwrap();
    fill(s, 4 * PAGE, 0x77);
    let v = reserve(4 * PAGE).unwrap();
    remap_shared_overwrite(v, s, 4 * PAGE).unwrap();
    allocate_fixed_overwrite(v + PAGE, PAGE).unwrap();
    fill(v + 3 * PAGE, 1, 0x78);
    assert_eq!(byte(s + 3 * PAGE), 0x78);
    assert_eq!(byte(v + PAGE), 0);
    for (r, l) in [(a, 4 * PAGE), (s, 4 * PAGE), (v, 4 * PAGE)] {
        deallocate(r, l);
    }
}

#[test]
fn protect_read_only_then_back() {
    let s = allocate_aliasable(4 * PAGE).unwrap();
    fill(s, 4 * PAGE, 1);
    protect(s + PAGE, PAGE, Prot::Read).unwrap();
    assert_eq!(byte(s + PAGE), 1, "still readable");
    assert_eq!(layout(s).len(), 3, "the protection cut the view into three");
    protect(s + PAGE, PAGE, Prot::ReadWrite).unwrap();
    fill(s + PAGE, 1, 2);
    assert_eq!(byte(s + PAGE), 2);
    deallocate(s, 4 * PAGE);
}

#[test]
fn views_of_a_foreign_range_like_qemus_guest_ram() {
    // QEMU makes the guest's RAM a section and maps it itself; the library aliases its pages.
    let section = Section::new(64 * PAGE).unwrap();
    let ram = reserve(64 * PAGE).unwrap(); // stands in for QEMU's own view
    map_section(ram, &section, 0, 64 * PAGE, Prot::ReadWrite).unwrap();
    for p in 0..64 {
        fill(ram + p * PAGE, PAGE, p as u8);
    }
    // Registered at another address too (QEMU's mapping is not in our region map).
    let qemu_view = reserve(64 * PAGE).unwrap();
    map_section(qemu_view, &section, 0, 64 * PAGE, Prot::ReadWrite).unwrap();
    let guard = register_foreign(qemu_view + 64 * PAGE * 1000, 64 * PAGE, Arc::clone(&section), 0);
    drop(guard);
    let foreign = register_foreign(0x7ff0_0000_0000, 64 * PAGE, Arc::clone(&section), 0).unwrap();
    let view = reserve(2 * PAGE).unwrap();
    remap_shared_overwrite(view, 0x7ff0_0000_0000 + 40 * PAGE, PAGE).unwrap();
    remap_shared_overwrite(view + PAGE, 0x7ff0_0000_0000 + 3 * PAGE, PAGE).unwrap();
    assert_eq!([byte(view), byte(view + PAGE)], [40, 3]);
    fill(view + 9, 1, 0xfe);
    assert_eq!(byte(ram + 40 * PAGE + 9), 0xfe);
    drop(foreign);
    assert_eq!(backing(0x7ff0_0000_0000, PAGE).err(), Some(Kern::INVALID_ADDRESS));
    for r in [ram, qemu_view, view] {
        deallocate(r, 64 * PAGE.min(64 * PAGE));
    }
}

#[test]
fn deallocating_everything_gives_the_address_space_back() {
    let a = allocate(4 * PAGE).unwrap();
    deallocate(a + PAGE, PAGE);
    assert_eq!(layout(a).iter().map(|p| p.2).collect::<String>(), "PRP");
    deallocate(a, PAGE);
    deallocate(a + 2 * PAGE, 2 * PAGE);
    assert!(layout(a).is_empty(), "the region went");
}
