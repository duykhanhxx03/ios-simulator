#!/bin/bash
# Build reims-vgpu's C library as a static library for the MSYS2 UCRT64 QEMU, and lay out the
# dist tree QEMU's meson finds through pkg-config (as `cargo xtask dist` does on Linux):
#   /d/vpw/dist/reims/{lib/libreims_vgpu.a, lib/pkgconfig/reims-vgpu.pc, include/reims_vgpu.h}
# MSYS2's own cargo (x86_64-pc-windows-gnu, UCRT) builds it, so it links with QEMU's gcc;
# RUSTC_BOOTSTRAP lets its stable rustc take the repository's -Z flags (.cargo/config.toml).
export MSYSTEM=UCRT64
source /etc/profile >/dev/null 2>&1 || true
set -euo pipefail
SRC="/d/ios simulator/refs/vphone-linux/reims-vgpu"
OUT=/d/vpw/dist/reims
export CARGO_TARGET_DIR=/d/vpw/build/reims
export RUSTC_BOOTSTRAP=1
CARGO=/ucrt64/bin/cargo
cd "$SRC"
"$CARGO" --version
"$CARGO" rustc -p reims-vgpu-c --release --features vulkan --crate-type staticlib \
	-- --print native-static-libs > /d/vpw/build/reims-build.log 2>&1 \
	|| { grep -E "^error|error\[" /d/vpw/build/reims-build.log | sort -u | head -40; tail -5 /d/vpw/build/reims-build.log; exit 1; }
LIBS=$(grep -o "native-static-libs: .*" /d/vpw/build/reims-build.log | tail -1 | sed 's/native-static-libs: //')
mkdir -p "$OUT/lib/pkgconfig" "$OUT/include"
# `cargo rustc --crate-type staticlib` leaves the archive in deps/, named after the crate.
cp "$(ls -t "$CARGO_TARGET_DIR"/release/deps/libreims_vgpu_c-*.a | head -1)" "$OUT/lib/libreims_vgpu.a"
# The import libraries rustc's native-static-libs name that come from crates (windows-targets'
# libwindows.0.NN.0.a), beside the archive so -L${libdir} finds them.
for lib in $(grep -o -- '-lwindows\.[0-9.]*' <<<"$LIBS" | sed 's/^-l//' | sort -u); do
	find "${CARGO_HOME:-/c/Users/$USER/.cargo}/registry/src" -name "lib$lib.a" -path "*x86_64_gnu*" \
		| head -1 | xargs -r -I{} cp {} "$OUT/lib/"
done
cp crates/reims-vgpu-c/include/reims_vgpu.h "$OUT/include/"
cat > "$OUT/lib/pkgconfig/reims-vgpu.pc" <<EOF
prefix=\${pcfiledir}/../..
libdir=\${prefix}/lib
includedir=\${prefix}/include

Name: reims-vgpu
Description: host side of Apple's paravirtualized GPU (reims-vgpu, Windows port)
Version: 0.1.0
Cflags: -I\${includedir}
Libs: -L\${libdir} -lreims_vgpu
Libs.private: $LIBS
EOF
ls -la "$OUT/lib/libreims_vgpu.a"
cat "$OUT/lib/pkgconfig/reims-vgpu.pc"
