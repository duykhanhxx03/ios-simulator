# ios-simulator

Dự án học tập: chạy máy ảo iOS trên Windows, local.

Hướng hiện tại là **iOS 26.6.2 (iPhone17,3) của [vphone-linux](https://github.com/YahiaAngelo/vphone-linux), port sang chạy native trên Windows**. Phần vẽ dùng GPU NVIDIA qua Vulkan:

- Guest dùng driver AppleParavirtGPU.
- [reims-vgpu](https://github.com/YahiaAngelo/reims-vgpu) dịch Metal sang Vulkan bằng [metal2vulkan](https://github.com/YahiaAngelo/metal2vulkan).
- RAM của guest được GPU đọc trực tiếp bằng `VK_EXT_external_memory_host`, không cần chép.

Kết quả đã đạt: iOS boot tới màn hình khoá và màn hình chính trên Windows 11, vẽ bằng RTX 5060. Cuộn Settings đạt khoảng 36–38 fps trên i5-12400F.

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| [vphone-win/](vphone-win/) | Phần port Windows: launcher `run-vphone.ps1`, các script build bằng MSYS2, công cụ (`vphoned.py`, `winbench.py`) và kế hoạch [PORT_PLAN.md](vphone-win/PORT_PLAN.md) |
| [patches/](patches/) | Các commit của dự án này trên repo của người khác, áp dụng bằng `git am` |
| [virt-ios/](virt-ios/) | Thử nghiệm iOS 16 trước đây (thiết bị QEMU ios-sweet), đã lưu trữ |
| [RESEARCH_AND_PLAN.md](RESEARCH_AND_PLAN.md) | Tổng hợp các repo máy ảo iOS và port iOS lên điện thoại Android |
| [GPU_PLAN.md](GPU_PLAN.md) | Nghiên cứu phần GPU (NVIDIA) |

## patches/

| Thư mục | Áp lên | Nội dung |
|---|---|---|
| `qemu-windows/` | [qemu-vphone](https://github.com/YahiaAngelo/qemu-vphone) `vphone` @ `86e6a93` | Build trên Windows, apple-gfx dùng reims-vgpu, `memory-backend-section`, vsock `virtio-vsock-sock-pci`, sửa lỗi luồng stdin quay vòng |
| `qemu-windows-perf/` | sau `qemu-windows/` | Hai nhánh tối ưu của upstream (`asid-ng`, `tb-recycle`) cộng 4 bản vá phía host Windows. **Chưa build và đo trên Windows** |
| `reims-vgpu/` | reims-vgpu `vphone-next4` | Nền tảng Windows: `reims-vgpu-winvm`, `os/windows`, thư viện C |
| `metal2vulkan/` | metal2vulkan `vphone-next4` | Runtime build được trên Windows; cỡ trang host |
| `vphone-linux-wsl/` | vphone-linux `main` | Các bản sửa khi tạo VM trên WSL2 (Ubuntu 26.04) |

## Chạy

Các bước:
1. Tạo VM trong WSL bằng `setup/create-vm.sh` của vphone-linux.
2. Chép VM sang Windows bằng `vphone-win/scripts/copy-vm-from-wsl.ps1`.
3. Tắt WSL rồi chạy máy ảo:

```powershell
wsl --shutdown
& .\vphone-win\run-vphone.ps1 -Vm F:\vpw\vms\<vm> -Window
```

Chi tiết build và các vấn đề đã gặp nằm trong [vphone-win/PORT_PLAN.md](vphone-win/PORT_PLAN.md).

## Lưu ý

- Repo không chứa firmware hay IPSW của Apple, cũng không chứa file của máy ảo.
- Các repo bên thứ ba có license riêng: QEMU là GPL-2.0, các repo còn lại xem license của từng repo.
