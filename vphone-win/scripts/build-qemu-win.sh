#!/bin/bash
# Build qemu-vphone (vresearch101) natively on Windows, MSYS2 UCRT64.
#   build-qemu-win.sh [--pvg]      (--pvg: link reims-vgpu, once its Windows port builds)
# Source is cloned to D:/vpw/src/qemu (QEMU's configure dislikes spaces in paths).
export MSYSTEM=UCRT64
# MSYS2's profile reads unset variables: load it before `set -u`.
source /etc/profile >/dev/null 2>&1 || true
set -euo pipefail
SRC_UP="/d/ios simulator/refs/vphone-linux/qemu"
SRC=/d/vpw/src/qemu
BUILD=/d/vpw/build/qemu
mkdir -p /d/vpw/src /d/vpw/build
if [[ ! -d $SRC/.git ]]; then
	git -c core.autocrlf=false clone -q "$SRC_UP" "$SRC"
fi
# The Windows fixes live on branch `windows` of $SRC (upstream fetched as FETCH_HEAD for diffing).
git -C "$SRC" fetch -q "$SRC_UP" HEAD
# The fork's own submodule (util/mlib, M*LIB headers); QEMU's meson subprojects are wraps.
git -C "$SRC" -c core.autocrlf=false submodule update --init -q util/mlib
PVG=--disable-pvg
if [[ ${1:-} == --pvg ]]; then
	# reims-vgpu's dist tree (scripts/build-reims-win.sh), linked statically into apple-gfx.
	PVG=--enable-pvg
	BUILD=/d/vpw/build/qemu-pvg
	export PKG_CONFIG_PATH=/d/vpw/dist/reims/lib/pkgconfig
fi
mkdir -p "$BUILD" && cd "$BUILD"
if [[ ! -f build.ninja ]]; then
	"$SRC/configure" --target-list=aarch64-softmmu --disable-werror \
		--enable-sdl --enable-slirp --enable-zstd --enable-png --enable-lzfse --enable-nettle --disable-qom-cast-debug $PVG > configure.log 2>&1 \
		|| { tail -30 configure.log; exit 1; }
fi
ninja -j12 -k 0 qemu-system-aarch64.exe > build.log 2>&1 || { grep "error:" build.log | sort -u | head -40; exit 1; }
ls -la qemu-system-aarch64.exe
./qemu-system-aarch64.exe -M help | grep -i -E "vresearch|virt " || true
