# virt-ios

Máy ảo iOS 16 trên QEMU (máy `virt`) dùng **kernel XNU mã nguồn mở đã vá của
[ios-sweet](https://github.com/dipa-studio/ios-sweet)** và **userland iOS 16.7.16 gốc
của iPhone X**. Toàn bộ chạy local trong WSL2 trên Windows. Mục đích: học tập.

Ý tưởng chính: không giả lập SoC Apple như Inferno, mà để QEMU giả lập đúng những phần
cứng tối thiểu mà kernel ios-sweet mong đợi (vốn được viết cho Redmi Note 10 Pro). Nhờ
vậy dùng nguyên **kernel ios-sweet build sẵn từ CI**, không cần máy Mac.

## Thành phần

| Đường dẫn | Nội dung |
|---|---|
| `qemu/hw/misc/sweet_ufs.c` | `sweet-ufshc`: host UFSHCI tối giản (UTRL slot, COMMAND UPIU, SCSI TUR/INQUIRY/READ CAP/READ/WRITE/SYNC, block 4 KiB). Khớp với driver `bsd/dev/sweet_ufs.c` |
| `qemu/hw/display/sweet_fb.c` | `sweet-fb`: hiển thị framebuffer a8r8g8b8 1080×2400 tại RAM `0x9c000000`, không copy |
| `qemu/apply.py` | Chép hai thiết bị vào cây QEMU 11.0.3, thêm vào meson, thêm tuỳ chọn máy `virt,sweet=on` (UFS ở `0x0b000000`, SPI 110, drive `ufs0`) |
| `tools/mkadt_virt.py` | ADT dạng `--qemu` của ios-sweet + node `/sweet-ufs` trỏ vào `0x0b000000` + danh tính `/product` của D22AP |
| `tools/mkdisk.py` | Bọc image root HFS+ vào disk GPT sector 4 KiB, phân vùng `userdata` (dư 8 MiB cho vòng log của kernel) |
| `tools/run.py` | Nạp payload (giống `qemu-sweet.py`) và chạy QEMU với `sweet=on` |
| `tools/ipsw_fetch.py` | Tải từng file trong IPSW qua HTTP range (zip64) |
| `tools/symbolize.py` | Tra địa chỉ trong panic sang symbol của kernel |
| `scripts/*.sh` | `payload.sh`, `smoke.sh`, `build-root.sh` (chạy trong WSL) |

## Môi trường
- WSL distro `ubuntu-ios` (Ubuntu 24.04) tại `D:\wsl\ubuntu-ios`. Thư mục làm việc `/root/vios`.
- QEMU v11.0.3 đã patch: `/opt/src/qemu/build/qemu-system-aarch64`.
- Kernel: ios-sweet CI run 39158169212 (patch 0081) → `D:\ios-work\kernels\0081`.
- IPSW iPhone10,3 16.7.16 (20H392), chỉ các file cần thiết → `D:\ios-work\ipsw`.

## Chạy (trong WSL, quyền root)
```sh
V="/mnt/d/ios simulator/virt-ios"
bash "$V/scripts/payload.sh" payload.bin            # kernel 0081 + virt.adt + boot-args
python3 "$V/tools/run.py" payload.bin run1 --disk ios16-disk.img            # cửa sổ GTK
python3 "$V/tools/run.py" payload.bin run1 --disk ios16-disk.img --display none --secs 150
```
Boot-args bắt buộc trên `virt`: `sweetkeys=0 sweettouch=0 sweetusb=0` (máy `virt` không có
khối PON, cảm ứng Goodix/GENI và USB của Qualcomm).

## Nhật ký
- **2026-11-13 – M1:** kernel 0081 boot trên `virt,sweet=on`, driver UFS nhận `sweet-ufshc`,
  tìm được GPT `userdata`, mount root HFS+ (panic tiếp theo là `exec /sbin/launchd errno 2`,
  đúng như kỳ vọng với disk rỗng). Panic đầu tiên (`sweet_keys_init`, external abort) được
  xử lý bằng `sweetkeys=0`.
- **2026-11-13/14 – M2:** root iOS 16.7.16 thật (mkiosroot + sweet-root-tweaks) boot tới
  SpringBoard. Lần boot đầu kẹt ở logo vì lsd dựng lại LaunchServices database (SpringBoard chờ
  `__LAUNCH_SERVICES_IS_RESEEDING_ITS_DATABASE_AND_MAY_BLOCK__`, tra bằng `ipsw dyld a2s`).
  SMP 6 nhân (QEMU `sweet=on`: MPIDR Aff1 + PSCI qua `smc`; ADT `--cpus 6`) → backboardd bật
  màn hình ở giây ~104. Cần `ml-timeout-global-scale=100` (spinlock timeout 48 ms quá ngắn với TCG).
  Màn hình tự tắt sau ~23 s (auto-lock, chưa có input) → `scripts/set-pref.sh … SBAutoLockTime -1`.
- **Dừng (2026-11-14):** người dùng yêu cầu bắt buộc vẽ bằng GPU. iOS 16 không có driver GPU ảo,
  nên dự án chuyển sang vphone-linux (iOS 26, reims-vgpu) + port Windows. Dữ liệu iOS 16
  (distro WSL, IPSW, disk) đã xoá; mã nguồn ở đây giữ lại để tham khảo.
