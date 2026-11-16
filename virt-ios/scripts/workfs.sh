#!/bin/bash
# Stage the iOS 16.7.16 dyld shared cache (3.2 GB) on a tmpfs at /mnt/fw.
# ipsw needs mmap, which WSL's 9P mounts (/mnt/d, /mnt/f) and apfs-fuse lack;
# the WSL vhdx lives on D:, which is nearly full, so RAM is used instead.
# Source copy: F:\ios-work\dsc (extracted once from the SystemOS cryptex).
set -euo pipefail
mkdir -p /mnt/fw
mountpoint -q /mnt/fw || mount -t tmpfs -o size=5g tmpfs /mnt/fw
mkdir -p /mnt/fw/dsc
cp -n /mnt/f/ios-work/dsc/dyld_shared_cache_arm64* /mnt/fw/dsc/
du -sh /mnt/fw/dsc
