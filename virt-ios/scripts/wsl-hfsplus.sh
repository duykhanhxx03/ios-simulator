#!/bin/bash
# Build and load hfsplus.ko for the running WSL2 kernel (it ships without HFS+).
# Uses Microsoft's WSL2-Linux-Kernel at the matching tag and the live config.
set -euo pipefail
REL=$(uname -r)                          # e.g. 6.18.58.1-microsoft-standard-WSL2
VER=${REL%%-*}
SRC=/opt/src/wsl-kernel-$VER
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq bc libelf-dev libssl-dev dwarves cpio > /dev/null
if [[ ! -d $SRC ]]; then
	git clone -q --depth 1 --branch linux-msft-wsl-$VER https://github.com/microsoft/WSL2-Linux-Kernel.git $SRC
fi
cd $SRC
zcat /proc/config.gz > .config
scripts/config --module CONFIG_HFSPLUS_FS --disable CONFIG_LOCALVERSION_AUTO
make olddefconfig > /dev/null
make LOCALVERSION= -j"$(nproc)" > /tmp/wslk.log 2>&1 || { tail -30 /tmp/wslk.log; exit 1; }
test "$(make -s LOCALVERSION= kernelrelease)" = "$REL" || { echo "release mismatch: $(make -s kernelrelease) != $REL"; exit 1; }
install -D -m644 fs/hfsplus/hfsplus.ko /lib/modules/$REL/extra/hfsplus.ko
depmod -a
modprobe hfsplus
grep hfsplus /proc/filesystems
