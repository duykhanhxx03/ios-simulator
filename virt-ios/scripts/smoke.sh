#!/bin/bash
# Smoke boot: ios-sweet kernel on virt-ios with a disk, headless, N seconds.
#   smoke.sh PAYLOAD DISK OUTNAME [SECS]
set -u
V="/mnt/d/ios simulator/virt-ios"
cd /root/vios
P=$1 D=$2 N=$3 S=${4:-150}
timeout $((S + 60)) python3 "$V/tools/run.py" "$P" "run-$N" --disk "$D" --display none --secs "$S" > "run-$N.log" 2>&1
grep -a -n -i -E "SWEET UFS|BSD root|panic|hfs: mounted|launchd|Waiting on|SWEET userspace|exit reason" "run-$N.log" | head -60
echo "----- tail"
tail -c 2000 "run-$N.log"
