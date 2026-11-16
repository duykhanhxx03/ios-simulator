#!/bin/bash
# Optimised variant of `build-qemu-win.sh --pvg`, in its own build dir (D:/vpw/build/qemu-pvg-opt),
# so it never touches the exe of a running VM.  Same configure options plus:
#   buildtype=release  -> -O3, no -g; debug=false also makes meson.build add -DNDEBUG -DG_DISABLE_ASSERT
#                         (this fork's include/qemu/osdep.h supports NDEBUG, unlike upstream QEMU)
#   b_lto=true, b_lto_threads=6   (gcc -flto=6)
#   x86_version=2      -> -mcx16 -mpopcnt -msse4.2 (Nehalem+ baseline).  NOT 3/-march=native:
#                         -mavx/-mavx2 globally is unsafe with mingw-w64 gcc (GCC PR 54412: 32-byte
#                         aligned stack spills of __m256 on a 16-byte aligned Win64 stack -> crashes).
#                         QEMU already picks AVX2 at runtime where it matters (bufferiszero, TCG host).
#   -mtune=alderlake   -> scheduling only, no new instructions.
# Run at BelowNormal priority so a running VM is not starved:
#   PowerShell: Start-Process -Priority BelowNormal is not available; use
#   cmd /c start "" /belownormal /wait D:\msys64\usr\bin\bash.exe -lc "/d/ios\ simulator/vphone-win/scripts/build-qemu-win-opt.sh"
export MSYSTEM=UCRT64
source /etc/profile >/dev/null 2>&1 || true
set -euo pipefail
SRC=/d/vpw/src/qemu
BUILD=/d/vpw/build/qemu-pvg-opt
JOBS=${JOBS:-6}
export PKG_CONFIG_PATH=/d/vpw/dist/reims/lib/pkgconfig
mkdir -p "$BUILD" && cd "$BUILD"
if [[ ! -f build.ninja ]]; then
	"$SRC/configure" --target-list=aarch64-softmmu --disable-werror \
		--enable-sdl --enable-slirp --enable-zstd --enable-png --enable-lzfse --enable-nettle \
		--disable-qom-cast-debug --enable-pvg \
		-Dbuildtype=release -Ddebug=false -Doptimization=3 \
		-Db_lto=true -Db_lto_threads=$JOBS -Dx86_version=2 \
		--extra-cflags=-mtune=alderlake > configure.log 2>&1 \
		|| { tail -30 configure.log; exit 1; }
fi
ninja -j$JOBS -k 0 qemu-system-aarch64.exe > build.log 2>&1 || { grep "error:" build.log | sort -u | head -40; exit 1; }
ls -la qemu-system-aarch64.exe
