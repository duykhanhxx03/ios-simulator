# vphone-win: vphone-linux (iOS 26) chạy native trên Windows, GPU bằng RTX 5060

> Mục tiêu: iOS **bắt buộc vẽ bằng GPU** NVIDIA thật. Trong WSL2, reims-vgpu chỉ dùng được lavapipe (CPU), nên phải chạy QEMU + reims-vgpu + metal2vulkan **native trên Windows**.
> Dựa trên: [vphone-linux](https://github.com/YahiaAngelo/vphone-linux) (QEMU fork `vresearch101`, reims-vgpu và metal2vulkan nhánh `vphone-next4`). Tham khảo: bản Windows của tqmane (`tqmane/reims-vgpu`, `tqmane/qemu-reims-vgpu`, chạy guest macOS khoảng 5 fps theo cách chép dữ liệu).

## Kết quả spike (2026-11-14) — `spikes/hostvm-import/spike.c`
| Kiểm tra | Kết quả |
|---|---|
| RAM guest = section pagefile (`CreateFileMapping`) | OK |
| View xáo trộn các trang guest ở mảnh **16 KiB** và **4 KiB** (placeholder `VirtualAlloc2` + `MapViewOfFile3` + `MEM_REPLACE_PLACEHOLDER`) | OK: đọc và ghi dùng chung với view gốc |
| `VK_EXT_external_memory_host` trên RTX 5060 (driver 616.92) | `minImportedHostPointerAlignment` = 0x1000, memoryTypeBits 0xc |
| Import **một vùng ghép từ 16 view riêng** thành một `VkDeviceMemory` | OK |
| GPU fill/copy qua vùng đó, kiểm tra bằng view gốc của guest | Đúng từng byte, 0,34 ms |

Kết luận: tương đương trên Windows của cơ chế Linux mà reims-vgpu dùng (mmap view của file RAM guest + import con trỏ host) **hoạt động trên NVIDIA**, không cần chép dữ liệu.

## Ánh xạ lớp nền tảng (reims-vgpu-vulkan `src/os/linux/*` → `src/os/windows/*`)
| Linux (dòng) | Vai trò | Bản Windows |
|---|---|---|
| `vm.rs` (1882) | `HostVm`: allocate/remap/protect/deallocate kiểu Mach | Section + placeholder: `VirtualAlloc2(MEM_RESERVE_PLACEHOLDER)`, tách bằng `VirtualFree(MEM_PRESERVE_PLACEHOLDER)`, `MapViewOfFile3(MEM_REPLACE_PLACEHOLDER)`, trả lại placeholder bằng `UnmapViewOfFile2(MEM_PRESERVE_PLACEHOLDER)`, `VirtualProtect` |
| `files.rs` (1056) | Bảng backing: địa chỉ → (fd, offset) của RAM guest | Bảng địa chỉ → (HANDLE section, offset). QEMU truyền handle section qua ABI thay cho fd |
| `import.rs` (1188), `dma_buf.rs` (853), `device_lend.rs` (502) | Import view: con trỏ host hoặc dma-buf | Chỉ dùng con trỏ host (`VK_EXT_external_memory_host`). Bỏ nhánh dma-buf/udmabuf |
| `host_ram.rs` (947) | Chép từng đoạn RAM guest (dự phòng) | Giữ logic, đổi sang ReadProcessMemory-free memcpy trên view |
| `surface.rs` (1317) | IOSurface giả lập trên memfd | Section ẩn danh cho mỗi surface (thay `memfd_create`) |
| `clock.rs` (204), `threads.rs` (177) | Đồng hồ, luồng | `QueryPerformanceCounter`/`GetSystemTimePreciseAsFileTime`, `SetThreadDescription`/priority |
| `budget.rs` (538), `hints.rs` (262), `dirty.rs` (393) | Ngân sách bộ nhớ, gợi ý, dirty log | `GlobalMemoryStatusEx`; dirty log ban đầu tắt (dự phòng chép toàn bộ) |
| `wsi.rs` + `WINDOW_SURFACE_EXTENSIONS` | Bề mặt cửa sổ | `VK_KHR_win32_surface` (SDL3 có sẵn trên Windows) |
| `open_loader` | `libvulkan.so` | `vulkan-1.dll` |

## Phía QEMU (qemu-vphone → build MSYS2 UCRT64)
1. RAM guest là **section có handle** (memory backend mới dựa trên `CreateFileMapping`, căn 2 MiB) và truyền handle + layout cho reims-vgpu.
2. `apple-gfx-mmio` + reims-vgpu linking tĩnh (Rust target `x86_64-pc-windows-gnu`).
3. vsock (vphoned): thay `vhost-vsock` bằng datapath Unix socket (Windows có AF_UNIX) hoặc TCP.
4. TCG trên Windows (không có KVM; guest arm64 vốn đã chạy TCG).
5. Tạo VM (restore DFU/usbip, APFS): **vẫn làm trong WSL** (distro `vphone`), sau đó chạy bundle VM bằng QEMU Windows.

## Các mốc
- [x] **M0** spike placeholder + import NVIDIA
- [ ] **M1** tạo VM iOS 26.6.2 (iPhone17,3) trong WSL, boot bằng lavapipe làm mốc so sánh
- [x] **M2** toolchain Windows: Rust 1.100 (MSVC), MSVC + Windows SDK 10.0.22621, MSYS2 UCRT64 tại `D:\msys64` (gcc 16.2, meson 1.12, rustc 1.99, glib, pixman, SDL2/3, libslirp, libpng, nettle/gmp, lzfse tự build)
  - ✅ **`qemu-system-aarch64.exe` build native** (D:\vpw\build\qemu, nguồn D:\vpw\src\qemu nhánh `windows`, commit `ec06017`), liệt kê máy `vresearch101`. 3 bản vá: `dev-hid` (CLOCK_BOOTTIME), `helper` (perf_event chỉ Linux), `t8030` (pwrite). Script: `scripts/build-qemu-win.sh`, `scripts/build-lzfse-win.sh`
- [~] **M3** `os/windows`: `HostVm`, backing table, import con trỏ host, clock/threads/surface
  - ✅ metal2vulkan (translator, AIR→SPIR-V) build được trên Windows như vốn có
  - ✅ metal2vulkan-runtime build được trên Windows: commit `bd9fb6b` nhánh `windows`
  - ✅ **reims-vgpu-vulkan build được trên Windows** (0 lỗi): commit `07471e0d` nhánh `steelbrain/windows-platform`. `reims-vgpu-winvm` (10/10 test), `os/windows/{clock,threads,vm,files,import,surface,host_ram}`
  - ✅ `reims-vgpu-window` (SDL3, build SDL từ nguồn bằng MSVC) build được trên Windows: dùng chung `native/linux` (SDL), socket điều khiển `REIMS_VGPU_WINDOW_CONTROL` tắt trên Windows (chưa commit)
  - ⏳ `reims-graphics` (thiết bị cho guest macOS x86-64, không dùng cho vphone) chưa build trên Windows (mmap, poll/fcntl wakeup, clock_gettime): kế hoạch giữ C API `rvg_graphics_*` nhưng trả "unsupported" trên Windows
  - ⏳ test Windows cho os/windows (bộ test Linux đang tắt); `reims-vgpu-c` hoàn chỉnh; liên kết vào QEMU (`--enable-pvg`, meson hiện chỉ cho darwin/linux)
- [x] **M1** VM iOS 26.6.2 trong WSL (2026-11-14): `create-vm.sh --device iPhone17,3 --ios 26.6.2 --scale 1` xong, cache ở `F:\ios-work\vphone-cache`. Ba bản vá trong clone WSL (chưa gửi upstream):
  - `linux-create/overlay/VPhoneCoreKit/Process/VPhoneProcessRunner.swift`: `runCapturing` đọc pipe bằng thread chặn; `readabilityHandler` của Foundation trên Linux bỏ lỡ EOF sau `losetup` → `cfw install` treo mãi ở `wasInstalled=false`
  - `tools/auto-lock-never.py`: thử lại khi `script.eval` vượt 30 s của vphoned (guest chậm ngay sau boot đầu)
  - first boot dính `SpringBoard.migrator hang` (đã ghi trong docs/WSL.md): chạy lại với `SMP=6 LP_NUM_THREADS=2`
  - hình nền đen = MercuryPosterExtension bị watchdog khởi động 30 s giết vì guest thiếu CPU: `tools/service-profile.py 3 trimmed` + `FBLaunchWatchdogScale=40` → hết (Weather trống vì weatherd bị tắt)
- vphone-linux WSL setup: bản vá `setup/setup.sh` (điều kiện cây kernel WSL phải có `build/Makefile`, Ubuntu 26.04 có thư mục header rỗng) — nên gửi upstream
- [x] **M4** QEMU: RAM dạng section, gắn reims-vgpu, boot tới màn hình khoá **vẽ bằng RTX 5060** — ✅ 2026-11-14 18:42: iOS 26.6.2 lên lock screen trên Windows native, metal2vulkan báo `the device NVIDIA GeForce RTX 5060`, cửa sổ `vphone-win` 430x932 (`run-vphone.ps1 -Vm F:\vpw\vms\duykhanhxx03 -Window -LogDir F:\vpw\runs\boot1`, ~10 phút từ iBoot)
  - ✅ QEMU `08c4a26`: thêm `memory-backend-section` vào enum `ObjectType`/`ObjectOptions` của `qapi/qom.json` (thiếu thì `-object` từ chối, dù `,help` vẫn liệt kê)
  - ✅ `run-vphone.ps1` khớp `run-vphone.sh` (`-DryRun`, chardev stdio + serial.log, không vsock, loader tự chọn ICD NVIDIA); `scripts/copy-vm-from-wsl.ps1` chép bundle giữ `Disk.img` sparse trên NTFS (drvfs `cp --sparse` không giữ được)
  - cần `wsl --shutdown` trước khi boot: commit trống < 8 GB khi WSL chạy
  - ✅ C library reims-vgpu build trên Windows (commit `76579074`): `reims-graphics` chỉ Unix, C API `rvg_graphics_*` trả UNSUPPORTED trên Windows; fd của memory file = HANDLE section (DuplicateHandle)
  - ✅ `libreims_vgpu.a` (windows-gnu, UCRT64, cargo của MSYS2 + RUSTC_BOOTSTRAP): `scripts/build-reims-win.sh` → `D:\vpw\dist\reims`
  - ✅ QEMU **có GPU** `D:\vpw\build\qemu-pvg\qemu-system-aarch64.exe` (359 MB): `memory-backend-section`, `apple-gfx` lấy HANDLE section, meson bật PVG cho TCG Windows. Chạy từ PowerShell (bash MSYS2 báo thiếu API set `api-ms-win-core-realtime`, bỏ qua được)
- [~] **M5** cửa sổ/input (SDL3), vsock, đo fps; gửi patch ngược cho upstream
  - ✅ vsock: `virtio-vsock-sock-pci` (QEMU `2dbaf4c`), host TCP 127.0.0.1:13390, `tools\vphoned.py`; vphoned trả health 200 trên iOS thật. Đã dùng để đặt Reduce Motion/Transparency, `BKNoWatchdogs`, múi giờ Asia/Ho_Chi_Minh, `system.shutdown`
  - ✅ đo fps: `tools\winbench.py` / `bench-run.ps1` (cuộn Settings như `scroll-measure.sh`, 3 lượt)
  - Kết quả (fps trung bình, i5-12400F + RTX 5060): 21.8 ban đầu → 35.8 (SMP 6 + NVIDIA "Prefer maximum performance") → 36–38 (448x932). Bản `-O3`/LTO + 4 bản vá Windows: 33–35, không hơn
  - ✅ sửa luồng stdin quay vòng (QEMU `b11ed48`, trước đó tốn trắng 1 core); tắt implicit Vulkan layer (TikTok LIVE Studio, Epic overlay) trong launcher
  - Nhánh QEMU `windows-perf` (chưa build/đo trên Windows): `asid-ng`, `tb-recycle` của upstream + 4 bản vá host. Nhánh `windows` = bản đang dùng
  - Giới hạn chính là CPU (TCG): GPU chỉ dùng khoảng 3%, mỗi frame khoảng 0.4 ms. Muốn mượt như máy thật thì cần chip ARM (Mac Apple Silicon + vphone-cli; nhánh `vphone-kvm` của upstream mới boot tới launchd trên M1 Pro chạy Asahi)
  - Còn mở: mạng (vphoned báo IP 10.0.2.15, chưa thử internet), âm thanh, gửi patch lên upstream
