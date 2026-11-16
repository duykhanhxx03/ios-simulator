#!/bin/bash
# class-dump one dylib of the iOS 16.7.16 dyld cache to /mnt/fw/headers/<name>.h
#   classdump.sh /System/Library/.../Binary [extra ipsw flags]
set -uo pipefail
D=/mnt/fw/dsc/dyld_shared_cache_arm64
mkdir -p /mnt/fw/headers
out=/mnt/fw/headers/$(basename "$1").h
ipsw class-dump "$D" "$@" > "$out" 2>&1
echo "$out: $(wc -l < "$out") lines"
