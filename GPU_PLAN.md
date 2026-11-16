# GPU cho máy ảo iOS trên Windows + NVIDIA: nghiên cứu và kế hoạch

> Ngày: 2026-11-13. Trạng thái các repo lấy từ README (do tác giả tự báo cáo), cần kiểm tra lại trước khi làm.
> Mục đích: học tập, chạy local. Không phát tán firmware, IPSW, cloudOS hay driver của Apple.

---

## 1. Kết luận chính

1. **Không nên viết GPU cho Inferno (iPhone 11, iOS 14).** iOS 14 chỉ có driver cho GPU thật của Apple (AGX). Muốn tăng tốc thì phải giả lập phần cứng AGX: firmware RTKit chạy trên coprocessor ASC, định dạng command buffer riêng, và **ISA shader của Apple phải dịch sang SPIR-V**. Asahi Linux mất nhiều năm mới viết xong driver cho chiều ngược lại. Làm việc này một mình là không khả thi.
2. **Hướng đúng là GPU ảo hoá (paravirtual) mà Apple đã có sẵn.** Từ iOS 26, thiết bị ảo `vphone600ap` / `vresearch101` (lấy từ firmware Private Cloud Compute) đi kèm:
   - kext `AppleParavirtGPU` trong kernelcache,
   - `AppleParavirtGPUMetalIOGPUFamily.bundle` (driver Metal chạy ở userland),
   - `libAppleParavirtCompilerPluginIOGPUFamily.dylib` (plugin biên dịch shader).

   Guest gửi luồng lệnh Metal (shader dạng AIR, tức LLVM bitcode) xuống host. Trên macOS, host xử lý bằng `ParavirtualizedGraphics.framework`.
3. **Phía host đã có người viết lại bằng mã nguồn mở và chạy được trên NVIDIA:**
   - **reims-vgpu**: viết lại `ParavirtualizedGraphics.framework`, chạy Metal của guest trên Vulkan.
   - **metal2vulkan**: dịch AIR sang SPIR-V và ánh xạ API Metal sang Vulkan.
   - **vphone-linux**: ghép hai phần trên với QEMU (`-M vresearch101`, TCG), OpenAVB và OpenSEP. Boot được iOS 26.6.2 / 27 lên home screen. **Đã thử trên RTX 4080 SUPER chạy Linux native**, cuộn Settings đạt khoảng 60 fps.
4. **Vấn đề của bạn nằm ở Windows:** trong WSL2, vphone-linux phải dùng **lavapipe (Vulkan bằng CPU)**, nên card NVIDIA không được dùng tới. Nguyên nhân:
   - Driver NVIDIA cho WSL chỉ cung cấp CUDA và D3D12, không có Vulkan.
   - Mesa `dzn` (Vulkan chạy trên D3D12) thiếu `VK_EXT_external_memory_host`, vì D3D12 trong WSL không có `ID3D12Device13::OpenExistingHeapFromAddress1`.
   - WSL2 không có `/dev/udmabuf`.
   - reims-vgpu cần một trong hai cơ chế trên để **import RAM của guest thành bộ nhớ Vulkan** mà không phải copy.

   → **Việc "viết GPU" có giá trị nhất với bạn: port host GPU (QEMU fork + reims-vgpu + metal2vulkan) sang chạy native trên Windows, dùng trực tiếp Vulkan của driver NVIDIA.** Trên Windows, driver NVIDIA có `VK_EXT_external_memory_host`, nên rào cản lớn nhất ở WSL không còn.

---

## 2. Kiến trúc tổng thể

```
┌──────────────── Guest iOS 26 (arm64e, chạy TCG) ────────────────┐
│ App → Metal.framework → AppleParavirtGPUMetalIOGPUFamily.bundle │
│        (shader: AIR) → IOGPUFamily → AppleParavirtGPU.kext      │
└──────────────┬──────────────────────────────────────────────────┘
               │ MMIO 0x30200000 (GPU, SPI 0x11)
               │ MMIO 0x30210000 (IOSurface mapper, SPI 0x10)
               │ command FIFO + tài nguyên nằm trong RAM của guest
┌──────────────▼──────────── QEMU (qemu-vphone) ──────────────────┐
│ hw/display/apple-gfx (bản port sang C, không phụ thuộc PGF)     │
│ RAM của guest = vùng bộ nhớ chia sẻ (Linux: /dev/shm, align 2MiB)│
└──────────────┬──────────────────────────────────────────────────┘
               │ C API (liên kết tĩnh)
┌──────────────▼──────────── reims-vgpu (Rust) ───────────────────┐
│ giải mã FIFO, quản lý resource/IOSurface/display,               │
│ mỗi task một tiến trình GPU riêng (process-isolation)           │
│   └─ metal2vulkan: AIR→SPIR-V, Metal API→Vulkan, giải mã ASTC   │
│      bằng CPU (NVIDIA không hỗ trợ ASTC)                        │
└──────────────┬──────────────────────────────────────────────────┘
               │ Vulkan 1.3 + VK_EXT_external_memory_host
           NVIDIA driver (Windows native) → RTX
               │
           SDL3 window (hiển thị + gửi input ngược vào QEMU)
```

---

## 3. Các repo cần đọc (theo thứ tự)

| # | Repo | Vai trò |
|---|---|---|
| 1 | [YahiaAngelo/vphone-linux](https://github.com/YahiaAngelo/vphone-linux) | Bộ ghép, launcher, tài liệu (`docs/ARCHITECTURE.md`, `WSL.md`, `PERFORMANCE.md`) |
| 2 | YahiaAngelo/qemu-vphone (nhánh `vphone`) | QEMU fork (dựa trên QEMUAppleSilicon/Inferno): `vresearch101`, CPU `apple-gxf`, SEP, `apple-gfx-mmio` |
| 3 | [steelbrain/reims-vgpu](https://github.com/steelbrain/reims-vgpu) | Host của giao thức PVG. Đọc `docs/api.md` và `docs/accuracy-ledger.md` |
| 4 | metal2vulkan ([docs.rs](https://docs.rs/metal2vulkan)) | Bộ dịch AIR sang SPIR-V |
| 5 | [darlinghq/indium](https://github.com/darlinghq/indium) (Iridium) | Một cách khác để chạy Metal trên Vulkan và dịch AIR sang SPIR-V, dùng để so sánh |
| 6 | [yaelliethy/orchard](https://github.com/yaelliethy/orchard/wiki) | Cùng kỹ thuật cho guest macOS trên TCG/Linux, tiền lệ của vphone-linux |
| 7 | [wh1te4ever/super-tart-vphone-writeup](https://github.com/wh1te4ever/super-tart-vphone-writeup) | Giải thích vì sao cần copy bundle Metal và dylib compiler plugin vào guest |
| 8 | QEMU `hw/display/apple-gfx*.m` ([patch của Phil Dennis-Jordan](https://patchew.org/QEMU/20241223221645.29911-1-phil@philjordan.eu/)) | Bản QEMU upstream gọi PGF trên macOS. Dùng để hiểu giao diện MMIO |
| 9 | [Asahi AGX docs](https://asahilinux.org/docs/hw/soc/agx/) | Kiến thức nền về GPU Apple (để hiểu vì sao không đi đường AGX) |
| 10 | gfxstream (Android Emulator) | Kiến trúc tham khảo cho việc stream lệnh GPU giữa guest và host trên Windows |

> ⚠️ Cẩn thận với các repo kiểu "vphone iOS for Windows" phát hành sẵn file .exe hoặc file nén có mật khẩu mà không kèm mã nguồn (ví dụ `Breead1337/vphoneiosforwin`, hiện đã trả về 404). Đây là mô-típ phát tán malware rất phổ biến.

---

## 4. Ba phương án cho host Windows + NVIDIA

| Phương án | Mô tả | Dùng NVIDIA? | Công sức | Đánh giá |
|---|---|---|---|---|
| **A. Dual-boot Linux** | Cài Fedora 43/44 (hoặc Arch) lên SSD hoặc USB SSD, dùng driver NVIDIA proprietary, chạy vphone-linux như README | Có, đã kiểm chứng | Thấp | Dùng làm **baseline** để đo và so sánh |
| **B. WSL2 + lavapipe** | Làm theo `docs/WSL.md` | Không, render bằng CPU | Thấp | Dùng để tạo VM (restore, CFW, APFS) và chạy thử |
| **C. Port native Windows** ⭐ | QEMU fork + reims-vgpu + metal2vulkan build bằng MSYS2/MSVC, GPU qua Vulkan của NVIDIA trên Windows | Có | Cao | **Dự án chính để học, và có giá trị đóng góp ngược cho upstream** |
| ~~D. Sửa dzn trong WSL~~ | Thêm external_memory_host cho dzn | — | — | Bế tắc: D3D12 trong WSL thiếu API, bạn không sửa được |
| ~~E. QEMU trong WSL, render ở Windows~~ | Chuyển lệnh qua hvsocket | Có | Cao | Không chia sẻ được RAM của guest qua ranh giới Hyper-V, phải copy liên tục nên chậm |

**Chiến lược đề xuất:** dùng B để **tạo VM** (các bước restore, `usbip` và APFS cần Linux). Dùng A làm mốc so sánh nếu có thể. Dồn công sức vào C để **chạy** chính bundle VM đó trên Windows.

---

## 5. Các vấn đề kỹ thuật khi port sang Windows

| Phụ thuộc Linux | Thay thế trên Windows |
|---|---|
| RAM guest là `/dev/shm` dạng shared file mapping | `memory-backend` dùng `CreateFileMapping` (pagefile-backed, `SEC_COMMIT`) + `MapViewOfFile` và tôn trọng align 2 MiB. QEMU Windows có `memory-backend-ram`, cần viết thêm một backend chia sẻ bằng section |
| Import RAM guest vào Vulkan qua `udmabuf`/dma-buf hoặc external_memory_host | Dùng **`VK_EXT_external_memory_host`** với `HOST_ALLOCATION_BIT_EXT`. Kiểm tra `minImportedHostPointerAlignment` và giới hạn kích thước của mỗi lần import. Có thể phải import theo từng vùng thay vì một heap 10 GB như orchard |
| `process-isolation` (tiến trình GPU riêng cho mỗi task, fork/memfd) | Giai đoạn đầu **tắt** (`process-isolation=off`). Sau đó dùng `CreateProcess` + `DuplicateHandle` để chia sẻ section |
| `vhost-vsock-pci` cho vphoned | Dùng datapath Unix-socket có sẵn (Windows 10+ hỗ trợ AF_UNIX) hoặc chuyển sang TCP |
| `vm.max_map_count` (tối ưu cho NVIDIA trên Linux) | Không cần. Thay vào đó cần chú ý giới hạn số lượng VkDeviceMemory (`maxMemoryAllocationCount`, trên NVIDIA thường là 4096) |
| Các bước tạo VM: `usbip`/`vusb`, `apfs-fuse`, `linux-apfs-rw` | Giữ nguyên trong WSL2. Bundle VM (Disk.img…) để trên ổ NTFS cho QEMU Windows đọc, hoặc chép sang |
| Kiến trúc CPU `apple-gxf`, TCG | Không phụ thuộc OS. TCG trên Windows (MinGW) chạy được, hiệu năng gần bằng Linux |
| SDL3 window + input | SDL3 hỗ trợ Windows |
| Build Rust (reims-vgpu, metal2vulkan) | `x86_64-pc-windows-gnu` để link với QEMU MinGW, hoặc build thành DLL với C ABI |

**Về khác biệt giữa Metal và Vulkan trên NVIDIA** (thuộc phạm vi metal2vulkan):
- Texture ASTC: NVIDIA desktop không hỗ trợ, hiện giải mã bằng CPU. **Nên chuyển sang compute shader**, đây là bài tập rất tốt.
- Programmable blending / framebuffer fetch: dùng `VK_KHR_dynamic_rendering_local_read`.
- Texture memoryless (dạng tile): dùng `TRANSIENT_ATTACHMENT` và `LAZILY_ALLOCATED`.
- Argument buffers: dùng `VK_EXT_descriptor_buffer` hoặc buffer device address.
- Pipeline cache: lưu `VkPipelineCache` xuống đĩa để lần khởi động app sau không phải biên dịch lại.

---

## 6. Kế hoạch theo giai đoạn

### Giai đoạn 0 – Kiểm tra máy (1 ngày)
- [ ] Chạy `vulkaninfo` trên Windows và ghi lại:
  - phiên bản API (cần 1.3 trở lên),
  - `VK_EXT_external_memory_host` và `minImportedHostPointerAlignment`,
  - `VK_KHR_dynamic_rendering_local_read`, `descriptor_buffer`, timeline semaphore,
  - `maxMemoryAllocationCount`, có hỗ trợ ASTC hay không.
- [ ] Phần cứng: RAM 32 GB (vphone-linux cần tối thiểu 16 GB, nên dư càng nhiều càng tốt), ổ trống 150 GB trở lên, CPU nhiều nhân (TCG mới là nút cổ chai thật sự).

### Giai đoạn 1 – Chạy được bản gốc (1–2 tuần)
- [ ] WSL2: cài Ubuntu 25.04 trở lên (24.04 quá cũ), làm theo `docs/WSL.md` và `CREATE-ON-LINUX.md` để tạo VM iPhone17,3 / iOS 26.6.2 với `--scale 1`.
- [ ] Boot trong WSL bằng lavapipe và đo fps (dùng `tools/` của repo, xem `PERFORMANCE.md`).
- [ ] (Nên làm) dual-boot Fedora lên SSD rời, chạy cùng bundle VM bằng NVIDIA để có **mốc hiệu năng mục tiêu**.

### Giai đoạn 2 – Hiểu đường GPU (2–3 tuần)
- [ ] Đọc mã `apple-gfx` trong qemu-vphone: các thanh ghi MMIO, ngắt, cách tìm FIFO trong RAM của guest.
- [ ] Đọc reims-vgpu: decoder của FIFO, vòng đời resource và IOSurface, cơ chế present, census mode và `accuracy-ledger.md`.
- [ ] Đọc metal2vulkan: dịch AIR sang SPIR-V cho một shader đơn giản. Dùng `spirv-dis` để xem kết quả.
- [ ] (Tuỳ chọn, dịch ngược) mở `AppleParavirtGPU` kext và Metal bundle bằng Ghidra hoặc REA để đối chiếu với giao thức mà reims-vgpu cài đặt.
- [ ] Dùng **RenderDoc** hoặc **Nsight Graphics** để capture một frame mà reims-vgpu gửi xuống Vulkan (thử trên Linux NVIDIA).

### Giai đoạn 3 – Port native Windows (4–8 tuần) ⭐
Các mốc:
- **M1. QEMU fork build được trên Windows (MSYS2 UCRT64), chưa có GPU.** Tắt `apple-gfx`. Boot tới mức có log serial hoặc kết nối được vphoned qua socket.
- **M2. Backend bộ nhớ chia sẻ** cho RAM của guest bằng Windows section, align 2 MiB.
- **M3. reims-vgpu chạy trên Windows**, `process-isolation=off`, import RAM của guest bằng `VK_EXT_external_memory_host` trên NVIDIA, present lên SDL3. **Mục tiêu: thấy màn hình khoá iOS được render bằng RTX.**
- **M4. Input** (cảm ứng/chuột, Home bằng Cmd+H sang phím khác trên Windows) và thanh nút.
- **M5. Process isolation** trên Windows bằng tiến trình con và section dùng chung.
- **M6. So sánh fps và độ trễ** với mốc Linux NVIDIA, viết script `run-vphone.ps1`.

### Giai đoạn 4 – Tối ưu và mở rộng GPU (tuỳ chọn)
- [ ] Giải mã ASTC bằng compute shader thay cho CPU.
- [ ] Lưu pipeline cache xuống đĩa và đo lại thời gian khởi động app (nguội và nóng).
- [ ] Xử lý dần các tính năng Metal mà census mode báo chưa mô hình hoá (`accuracy-ledger`).
- [ ] Gửi patch ngược cho reims-vgpu, metal2vulkan và vphone-linux (Windows platform).

---

## 7. Rủi ro
- **CPU mới là nút cổ chai thật sự:** guest arm64e chạy bằng TCG trên x86. GPU nhanh không làm boot nhanh hơn (boot mất vài phút, mở app nguội mất khoảng 20 giây).
- `VK_EXT_external_memory_host` trên NVIDIA Windows có thể giới hạn kích thước hoặc độ align khi import, nên có thể phải chia nhỏ vùng import và tính lại offset.
- Các upstream phát triển rất nhanh (dự án ra đời giữa và cuối năm 2026), các nhánh `vphone` có thể force-push. Nên fork và pin commit.
- Pháp lý: firmware cloudOS/PCC và IPSW thuộc Apple, có điều khoản riêng cho Virtual Research Environment. Chỉ dùng cá nhân, không phân phối.

---

## 8. Ghi chú repo tham khảo (bổ sung 2026-11-13)

### [nullmoth/nvidia-macos-driver](https://github.com/nullmoth/nvidia-macos-driver)
- **Là gì:** driver Metal cho GPU NVIDIA (Turing trở lên, gồm RTX 50) trên macOS 15.5+/26, chỉ x86_64 (Mac Intel hoặc máy chạy OpenCore). **Họ chủ yếu thử trên RTX 5060, đúng card của mình.**
- **Kiến trúc:** Metal app → `NVMTLDriver.bundle` (plugin driver Metal ở userland) → bộ dịch AIR sang SPIR-V (`translator/`, LGPL, dựa trên metal2vulkan) → **NVK** (Vulkan của Mesa, trình biên dịch NAK) → các kext NVRM/NVAccel/NVRMFB chạy open-gpu-kernel-modules của NVIDIA (r610/620) + firmware GSP gốc.
- **Hỗ trợ:** Metal 3 (argument buffers tier 2, ray tracing, mesh shader, MPS), OpenGL qua GL-on-Metal, OpenCL, Core Image, Core ML.
- **Giấy phép:** `plugin/`, `kexts/` theo PolyForm Noncommercial (dùng cho học tập và nghiên cứu được), `translator/` theo LGPL-3.0, `nvk/` theo MIT.
- **Giá trị với virt-ios (iOS 16):**
  1. iOS 16 **không có** driver GPU ảo hoá như iOS 26 (vphone). Muốn tăng tốc thì phải tự cung cấp một **plugin driver Metal** cho iOS. `NVMTLDriver.bundle` là mẫu mã nguồn mở hiếm hoi cài đặt được giao diện driver Metal riêng (private) của Apple. Đây là điểm khởi đầu tốt nhất thay vì tự dịch ngược AGXMetal.
  2. Đường đề xuất cho máy ảo: plugin Metal kiểu NVMTLDriver (bản arm64 cho iOS 16) → translator AIR sang SPIR-V → thay NVK bằng **Vulkan forwarding** (kiểu Venus/gfxstream: mã hoá lệnh Vulkan trong guest, giải mã trên host) → kext XNU vận chuyển dữ liệu (MMIO/ring, giống `sweet-ufshc`) → QEMU → **Vulkan của NVIDIA trên host**.
  3. Bộ translator dùng chung gốc metal2vulkan với reims-vgpu, nên kinh nghiệm ở hai hướng (iOS 16 và vphone/iOS 26) bổ trợ cho nhau.
- **Đã xác minh (2026-11-14, `ipsw class-dump` trên dyld cache iOS 16.7.16 20H392):**
  - Chuỗi kế thừa trên iOS 16: `AGXA11Device : AGXA11FamilyDevice : IOGPUMetalDevice : _MTLDevice`. Lớp `IOGPUMetalDevice` nằm trong `IOGPU.framework`. Trên macOS 15, NVMTL kế thừa lớp cũ `MTLIOAccelDevice` (`initWithAcceleratorPort:`).
  - `IOGPUMetalDevice` giữ `_deviceRef` (`__IOGPUDevice`: comm page, `IOGPUClientSharedGlobalRO`, `IOGPUDeviceConfigData`) và `_acceleratorPort`. Vậy phía kernel cần một user client **IOGPU** (không phải IOAccelerator2 như trên macOS).
  - Có hai cách cho plugin của virt-ios:
    - **(A)** kế thừa `IOGPUMetalDevice` và bổ sung vào kernel ios-sweet một user client IOGPU giả lập (giống vai trò NVAccel).
    - **(B)** kế thừa thẳng `_MTLDevice`, tự cài mọi object trên Vulkan như NVMTL làm với buffer, texture và command buffer. Cách này cần kiểm tra QuartzCore/CoreAnimation có gọi API riêng của IOGPU hay không.
  - Header đã dump nằm trong `/mnt/fw/headers/{AGXMetalA11,IOGPU,Metal}.h` (tmpfs trong WSL, tạo lại bằng `scripts/workfs.sh` + `scripts/classdump.sh`).
  - RTX 5060 (driver 616.92): Vulkan 1.4, có `VK_EXT_external_memory_host` (căn lề 4 KiB), `dynamic_rendering_local_read`, `descriptor_buffer`, mesh shader, ray query; **không có ASTC LDR**.
- **Rủi ro:** giao diện plugin Metal giữa macOS 15 x86_64 và iOS 16 arm64 có thể khác (phiên bản IOGPU/MTLDriver). Cần dịch ngược `AGXMetalA11` của iOS 16 để so sánh (dùng `/reverse-engineer-anything`). Phần kext trong repo phụ thuộc phần cứng thật, nên không dùng lại được cho máy ảo.

---

## Nguồn
- https://github.com/YahiaAngelo/vphone-linux · docs/WSL.md · docs/ARCHITECTURE.md
- https://github.com/steelbrain/reims-vgpu
- https://github.com/yaelliethy/orchard/wiki
- https://github.com/Lakr233/vphone-cli · https://github.com/wh1te4ever/super-tart-vphone-writeup
- https://github.com/LIGHTENINGXGAMES/vphone-gpu-driver-extract
- https://docs.rs/metal2vulkan · https://github.com/darlinghq/indium
- https://patchew.org/QEMU/20241223221645.29911-1-phil@philjordan.eu/ (QEMU apple-gfx / vmapple)
- https://asahilinux.org/docs/hw/soc/agx/ · https://alyssarosenzweig.ca/blog/asahi-gpu-part-1.html
- https://android.googlesource.com/platform/hardware/google/gfxstream
- https://github.com/ChefKissInc/Inferno
