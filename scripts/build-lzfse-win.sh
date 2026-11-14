#!/bin/bash
# Build Apple's lzfse (BSD-3-Clause) for MSYS2 UCRT64 and install it into /ucrt64
# (qemu-vphone's hw/arm/apple-silicon/boot.c decompresses firmware with it).
export MSYSTEM=UCRT64
source /etc/profile >/dev/null 2>&1 || true
set -euo pipefail
SRC=/d/vpw/src/lzfse
[[ -d $SRC/.git ]] || git clone -q --depth 1 https://github.com/lzfse/lzfse.git "$SRC"
mkdir -p /d/vpw/build/lzfse && cd /d/vpw/build/lzfse
cmake -G Ninja "$SRC" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/ucrt64 \
	-DLZFSE_BUNDLE_MODE=OFF -DBUILD_SHARED_LIBS=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 > cmake.log 2>&1 || { tail -20 cmake.log; exit 1; }
ninja > build.log 2>&1 || { tail -20 build.log; exit 1; }
ninja install > install.log 2>&1
ls -la /ucrt64/include/lzfse.h /ucrt64/lib/liblzfse*
