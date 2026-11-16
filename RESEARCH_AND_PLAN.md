# Máy ảo iOS trên Windows: tổng hợp nghiên cứu và kế hoạch (học tập, chạy local)

> **Cập nhật 2026-11-13 (sau khi tìm hiểu về GPU):** đã có [vphone-linux](https://github.com/YahiaAngelo/vphone-linux), chạy iOS 26/27 trên QEMU (`vresearch101`) và có GPU ảo hoá (reims-vgpu + metal2vulkan). Dự án hỗ trợ Windows 11 qua WSL2, nhưng ở đó chỉ render bằng CPU (lavapipe). Nền tảng được khuyến nghị **chuyển từ Inferno/iOS 14 sang vphone-linux**. Chi tiết và kế hoạch GPU nằm ở [GPU_PLAN.md](GPU_PLAN.md). Mục 3 và 4 bên dưới vẫn hữu ích cho phần học nền tảng.

> Ngày nghiên cứu: 2026-11-13. Thông tin trạng thái lấy từ README và bài viết công khai; trước khi bắt đầu hãy kiểm tra lại repo.
> Pháp lý: chỉ dùng IPSW tải trực tiếp từ Apple cho bản thân. Không phát tán firmware, key, IV hay image đã sửa. Giấy phép iOS/macOS của Apple không cho phép chạy ngoài phần cứng Apple, nên dự án này chỉ ở mức nghiên cứu cá nhân.

---

## 1. Bức tranh tổng quan

Có 4 cách tiếp cận "chạy iOS ngoài iPhone":

| Hướng | Cách hoạt động | Đại diện | Độ khó | Hợp với Windows |
|---|---|---|---|---|
| **A. Giả lập toàn hệ thống (LLE)** | QEMU giả lập SoC Apple (A13/T8030), chạy SecureROM → iBoot/kernel → iOS gốc | **Inferno** (ChefKiss), qemu-t8030, eShard, alephsecurity | Rất cao | Qua WSL2 hoặc build QEMU bằng MSYS2 |
| **B. Giả lập mức cao (HLE)** | Chạy binary app ARM, tự viết lại API UIKit/Foundation | **touchHLE**, ipasim | Trung bình | Native Windows |
| **C. Port lên phần cứng không phải Apple** | Vá XNU (mã nguồn mở) cho SoC Qualcomm, chạy userland iOS gốc | **ios-sweet** (Redmi Note 10 Pro), các fork Mi 11… | Rất cao | Không liên quan (chạy trên điện thoại) |
| **D. Ảo hoá macOS rồi dùng Xcode Simulator** | Chạy macOS trong KVM/QEMU, mở iOS Simulator | OSX-KVM, Docker-OSX | Thấp–trung bình | Kém (cần KVM, nên dùng Linux host) |

**Kết luận nhanh:** Muốn "máy ảo iOS thật" trên Windows để học thì nền tảng tốt nhất là **hướng A (Inferno)**. Hướng C (ios-sweet) không chạy trên Windows được, nhưng là tài liệu tham khảo rất giá trị về cách vá XNU, sinh Apple Device Tree và tắt AMFI/SEP. Nên học nó song song.

---

## 2. Các repo chính

### 2.1. Giả lập toàn hệ thống (QEMU)

| Repo | Thiết bị / iOS | Trạng thái | Ghi chú |
|---|---|---|---|
| [TrungNguyen1909/qemu-t8030](https://github.com/TrungNguyen1909/qemu-t8030) | iPhone 11 (T8030), iOS 14 | Đã archive | Gốc của mọi fork hiện đại. Wiki "USB and Restore" rất có ích |
| [ChefKissInc/Inferno](https://github.com/ChefKissInc/Inferno) (trước đây là QEMUAppleSilicon) | iPhone 11, iOS 14.0 beta 5 | Archive trên GitHub từ 2026-11-01, **chuyển sang git.chefkiss.dev/AppleHax/Inferno** | Còn phát triển. Restore/boot, render phần mềm, multitouch, mạng. GPL-3/AGPL-3. Docs: chefkiss.dev/applehax/inferno |
| eShard: [phần 1](https://eshard.com/blog/emulating-ios-14-with-qemu), [phần 2](https://www.eshard.com/blog/emulating-ios-14-with-qemu-part2) | iPhone 11, iOS 14.0 | Blog, không công bố code | **Đọc bắt buộc**: danh sách bản vá cụ thể để lên tới SpringBoard |
| [alephsecurity/xnu-qemu-arm64](https://github.com/alephsecurity/xnu-qemu-arm64) | Kernel iOS 12/13, chỉ ra shell | Cũ | Điểm khởi đầu nhẹ để học boot XNU trên QEMU, không có GUI |
| [devos50/qemu-ios](https://github.com/devos50/qemu-ios) | iPod touch 1G/2G, iPhoneOS 1.0 / 2.1 | Còn hoạt động (FOSDEM 2024) | SoC cũ (S5L8900), dễ hiểu hơn rất nhiều. **Lý tưởng để học** |
| [NixOS wiki – iOS Emulation](https://wiki.nixos.org/wiki/IOS_Emulation) | Hướng dẫn QEMU-AppleSilicon | | Hướng dẫn từng bước (disk image, ticket, SEP, idevicerestore) |

**Những gì đã chạy được với iOS 14 trên QEMU** (theo eShard và Inferno): restore qua `idevicerestore`, boot lên SpringBoard, render bằng CPU (QuartzCore software path), multitouch, SSH/serial, mạng qua reverse tethering của usbmuxd, cài IPA đã giải mã (AppSync + ideviceinstaller), debug qua gdbstub.

**Những gì chưa được giả lập và phải vá né:** SEP (MobileKeyBag, lockdownd), baseband (tắt CommCenter), GPU Metal, backlight/idle sleep, kích hoạt (mobileactivationd), datamigrator, Setup Assistant (purplebuddy.plist).

### 2.2. Giả lập mức cao (HLE)

| Repo | Ghi chú |
|---|---|
| [touchHLE/touchHLE](https://github.com/touchHLE/touchHLE) | Rust, chạy native trên Windows. App iPhone OS 2.x/3.x (32-bit, chủ yếu game). Kiến trúc sạch (dyld, ObjC runtime, UIKit/OpenGL ES tự viết), **rất đáng đọc để hiểu iOS ở tầng app** |
| [ipasimulator/ipasim](https://github.com/ipasimulator/ipasim) | C++, dựa trên WinObjC, ngừng phát triển |
| [microsoft/WinObjC](https://github.com/microsoft/WinObjC) | Bridge để biên dịch lại code ObjC, không phải emulator. Archive từ 05/2025 |
| [darlinghq/darling](https://github.com/darlinghq/darling) | Lớp tương thích macOS cho Linux (tương tự Wine). Không chạy trên WSL1, GUI còn hạn chế |

### 2.3. Port iOS lên điện thoại Android (Xiaomi, Samsung…)

| Repo | Thiết bị | Trạng thái |
|---|---|---|
| [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet) (upstream) | Redmi Note 10 Pro "sweet" (SM7150) | Từ 2026-10-18 boot tới home screen. Chạy userland iOS 16.7.16 của iPhone X không chỉnh sửa |
| [Arming1/ios-sweet](https://github.com/Arming1/ios-sweet) | Xiaomi Mi 11 "venus" | Thử nghiệm: lock screen, home, Settings, cảm ứng chạy |
| [DeclanFrampton/ios-sweet](https://github.com/DeclanFrampton/ios-sweet) | (fork) | Thêm bộ cài cho Windows/Docker |
| Samsung | Chưa có port nào chạy được (từng có người thử Galaxy S7 Exynos 8890 và Pixel 6 Tensor) | |

> Lưu ý: các "iOS cho Xiaomi/Samsung" trên mạng (theme MIUI, launcher, ROM "iOS") gần như đều chỉ là giao diện Android giả iOS. ios-sweet là dự án đầu tiên chạy **XNU + userland iOS thật**.

**Kỹ thuật của ios-sweet** (đáng học vì áp dụng ngược lại được cho máy ảo):
- Chuỗi boot: ABL (Xiaomi) → `fastboot boot shim-boot.img` → shim bare-metal (tự dựng MMU, đọc FDT) → payload gồm XNU Mach-O, **Apple Device Tree tự sinh** (`mkadt.py`) và boot-args.
- Khoảng 69 bản vá XNU (xnu-8796): bỏ PAC trên arm64, GICv3 thay cho AIC của Apple, timer qua IRQ thay vì FIQ, PSCI để bật nhân CPU, driver UFS, HFS+ root, IOMobileFramebuffer/AppleCLCD giả, HID cho touch và phím.
- AMFI/Sandbox để chế độ permissive, AppleKeyStore là stub, không có SEP, kích hoạt bằng "Hactivation", render bằng CPU khoảng 20–30 fps.
- Build kernel bằng GitHub macOS runner + `blacktop/darwin-xnu-build`. Root image ghép từ IPSW bằng `apfs-fuse` + `hfsprogs`. Có `tools/qemu-sweet.py` để test kernel trên QEMU.

### 2.4. Ảo hoá macOS (để dùng Xcode Simulator chính chủ)
- [kholia/OSX-KVM](https://github.com/kholia/OSX-KVM), [sickcodes/Docker-OSX](https://github.com/sickcodes/Docker-OSX): cần KVM (Linux host). Trên Windows chỉ chạy được qua WSL2 với nested virtualization, rất chậm và không có GPU. Đây là cách "thực dụng" nhất để chạy app iOS, nhưng **không phải** máy ảo iOS. EULA của Apple yêu cầu phần cứng Apple.

### 2.5. Thương mại (tham khảo)
- Corellium: ảo hoá iOS trên ARM server, mục tiêu research/pentest. Appetize.io: simulator trên cloud.

### 2.6. Công cụ hỗ trợ bắt buộc
- [blacktop/ipsw](https://github.com/blacktop/ipsw): tải/giải nén IPSW, img4, device tree, kernelcache, dyld cache, class-dump.
- [libimobiledevice](https://github.com/libimobiledevice) (idevicerestore, usbmuxd, iproxy, ideviceinstaller).
- [blacktop/darwin-xnu-build](https://github.com/blacktop/darwin-xnu-build), [apple-oss-distributions/xnu](https://github.com/apple-oss-distributions/xnu).
- [theos](https://github.com/theos/theos) và AppSync Unified để cài IPA không ký.
- Ghidra/IDA/Binary Ninja để vá binary userland (SpringBoard, backboardd…).

---

## 3. Lựa chọn kiến trúc cho dự án của bạn

**Khuyến nghị:** dựng **Inferno (QEMU, iPhone 11, iOS 14 beta 5)** trên **Windows 11 + WSL2 (Ubuntu 24.04)**.

Lý do:
- Guest là AArch64 còn host là x86, nên QEMU vẫn phải dùng TCG (dịch nhị phân). KVM không giúp gì, vì vậy WSL2 không bị thiệt so với Ubuntu native.
- WSLg hiển thị cửa sổ SDL/GTK của QEMU trực tiếp, hoặc dùng VNC.
- Toolchain (idevicerestore, apfs-fuse, ipsw) chạy mượt trên Linux. Build native Windows bằng MSYS2 là phương án thay thế, nhưng phần USB/usbmuxd sẽ khó hơn.

Phần cứng gợi ý: CPU 8 nhân trở lên, RAM 16–32 GB, SSD trống 64 GB trở lên.

---

## 4. Kế hoạch theo giai đoạn

### Giai đoạn 0 – Nền tảng (1–2 tuần)
- [ ] Đọc: kiến trúc boot của Apple (SecureROM → iBoot → kernelcache), IMG4/IM4P/IM4M, Apple Device Tree, trustcache, AMFI, SEP.
- [ ] Đọc eShard phần 1 và 2, wiki của qemu-t8030, README và `NOTES.md` của ios-sweet.
- [ ] Cài WSL2 Ubuntu 24.04, bật WSLg, cấu hình `.wslconfig` (memory=24GB, processors=8).
- [ ] Cài `ipsw`, `libimobiledevice`, `idevicerestore`, `apfs-fuse`, `hfsprogs`, Ghidra.

### Giai đoạn 1 – Khởi động bằng thiết bị cũ (1–2 tuần, nên làm để hiểu cơ chế)
- [ ] Build [devos50/qemu-ios](https://github.com/devos50/qemu-ios) và chạy iPod touch 1G (iPhoneOS 1.0) lên SpringBoard.
- [ ] Đọc mã máy ảo trong QEMU (`hw/arm/…`): cách giả lập interrupt controller, timer, framebuffer, NAND.
- [ ] (Tuỳ chọn) chạy alephsecurity/xnu-qemu-arm64 để thấy XNU 64-bit boot ra shell.

### Giai đoạn 2 – Dựng Inferno trên WSL2 (2–4 tuần)
- [ ] Clone từ `git.chefkiss.dev/AppleHax/Inferno` (GitHub đã archive), build `qemu-system-aarch64` với target Apple.
- [ ] Tải IPSW iPhone12,1 bản iOS 14.0 beta 5 (18A5351d) từ Apple. Giải nén bằng `ipsw`.
- [ ] Tạo các disk image (root, firmware, syscfg, nvram, effaceable, sep_nvram, sep_ssc…) theo NixOS wiki và docs Inferno.
- [ ] Chuẩn bị SEP firmware, trustcache, device tree, kernelcache, SEPROM theo hướng dẫn (tự tìm key, **không chia sẻ**).
- [ ] Dựng môi trường "companion" (VM hoặc chính WSL) chạy `usbmuxd` và `idevicerestore`, rồi restore.
- [ ] Áp các bản vá filesystem (datamigrator, activation, purplebuddy) và boot lên SpringBoard.
- **Mốc:** thấy màn hình home iOS 14 trong cửa sổ QEMU trên Windows.

### Giai đoạn 3 – Khả năng sử dụng (2–3 tuần)
- [ ] SSH vào guest qua `iproxy`, bật mạng bằng reverse tethering (usbmuxd mode 3 + dnsmasq).
- [ ] Cài AppSync và `ideviceinstaller`, chạy một IPA tự build (app Hello World viết bằng theos hoặc Xcode, đã giải mã).
- [ ] Viết script tiện ích trên Windows: `start-ios.ps1` (gọi `wsl` để chạy QEMU), snapshot qcow2 để lưu và khôi phục nhanh.
- [ ] Debug: kết nối `gdb-multiarch`/lldb vào gdbstub của QEMU, đặt breakpoint trong kernel và SpringBoard.

### Giai đoạn 4 – Đào sâu: tự làm một phần (tuỳ mức độ)
Chọn một hoặc nhiều hướng:
1. **Viết thiết bị QEMU mới**: ví dụ giả lập cảm biến, pin hoặc âm thanh tối giản, theo phong cách của Inferno.
2. **Thử iOS mới hơn** (15/16): so sánh kernelcache và device tree, ghi lại các panic, vá dần (giống lộ trình eShard đề ra).
3. **Mang kỹ thuật của ios-sweet vào QEMU**: dựng máy ảo "virt-ios" dùng GICv3 + PL011 + virtio, cùng XNU tự build từ mã nguồn mở (xnu-8796 + các bản vá non-PAC/GICv3 của ios-sweet), và userland iOS 16. Điểm hay là không cần giả lập SoC Apple. ios-sweet đã có `tools/qemu-sweet.py` làm điểm khởi đầu. **Đây là hướng mới, có giá trị học tập cao nhất.**
4. **Hướng HLE**: đóng góp cho touchHLE (Rust), cài đặt thêm API UIKit.

### Giai đoạn 5 – Ghi chép
- [ ] Viết nhật ký kỹ thuật (giống `NOTES.md` của ios-sweet): mỗi panic, nguyên nhân, bản vá.

---

## 5. Rủi ro và giới hạn thực tế
- **Hiệu năng:** TCG + render bằng CPU sẽ chậm (boot vài phút, UI khoảng 10–30 fps). Không có GPU/Metal nên phần lớn game và app hiện đại sẽ không chạy.
- **Phiên bản iOS:** gần như chỉ có iOS 14 beta là ổn định trên QEMU. App từ App Store cần Apple ID, FairPlay và SEP, nên **không chạy được**. Chỉ chạy được IPA đã giải mã hoặc tự build.
- **Dự án upstream có thể thay đổi hoặc biến mất** (Inferno vừa rời GitHub). Hãy mirror repo về local.
- **Pháp lý:** chỉ dùng cho học tập cá nhân, không phân phối firmware hay image.

---

## Nguồn
- https://github.com/TrungNguyen1909/qemu-t8030 · https://github.com/TrungNguyen1909/qemu-t8030/wiki/USB-and-Restore
- https://github.com/ChefKissInc/Inferno · https://awesome.ecosyste.ms/projects/github.com%2FChefKissInc%2FQEMUAppleSilicon
- https://wiki.nixos.org/wiki/IOS_Emulation
- https://eshard.com/blog/emulating-ios-14-with-qemu · https://www.eshard.com/blog/emulating-ios-14-with-qemu-part2
- https://hackaday.com/2025/04/06/emulating-iphone-on-qemu
- https://github.com/devos50/qemu-ios · https://github.com/devos50/qemu-ios-generate-nor · https://9to5mac.com/2023/11/07/qemu-iphone-os-emulator/
- https://github.com/touchHLE/touchHLE · https://github.com/ipasimulator/ipasim · https://github.com/microsoft/WinObjC
- https://github.com/dipa-studio/ios-sweet · https://github.com/Arming1/ios-sweet · https://github.com/DeclanFrampton/ios-sweet
- https://www.phonearena.com/news/someone-successfully-booted-ios-on-an-android-device_id184233 · https://decrypt.co/381351/hackers-running-apple-ios-android-phones
- https://emulation.gametechwiki.com/index.php/iOS_emulators
- https://linuxiac.com/?p=222800 (Darling)
