#!/bin/bash
# Snapshot the framebuffer of a running VM every N seconds into F:\ios-work\runs\<run>\NNNN.png
#   watch.sh RUNDIR [INTERVAL] [COUNT]
set -u
RUN=$1 IV=${2:-10} N=${3:-360}
OUT=/mnt/f/ios-work/runs/$(basename "$RUN")
mkdir -p "$OUT"
for i in $(seq -w 1 "$N"); do
	sleep "$IV"
	pgrep -x qemu-system-aar >/dev/null || break
	python3 "/mnt/d/ios simulator/virt-ios/tools/snap.py" "/root/vios/$RUN" "$OUT/$i.png" > /dev/null 2>&1
	cp "$OUT/$i.png" "$OUT/latest.png" 2>/dev/null
done
