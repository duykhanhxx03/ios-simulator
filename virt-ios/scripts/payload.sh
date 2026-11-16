#!/bin/bash
# Build a virt-ios payload from the ios-sweet kernel + virt ADT.
#   payload.sh OUT.bin [EXTRA BOOT-ARGS...]
set -eu
V="/mnt/d/ios simulator/virt-ios"
cd /root/vios
OUT=$1; shift
# Phone-only hardware (PON keys, Goodix touch over GENI, Qualcomm USB) is absent on virt.
# XNU takes the FIRST occurrence of a boot-arg: set the CPU count with CPUS=N, not an extra cpus=.
BASE="debug=0xc serial=3 cpus=${CPUS:-1} keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 ufsrw=1 rootrw=1 wdt=-1 wdt_test=no_user_checkin launchd_unsecure_cache=1 unentitled_ios_sim_launch=1 ignition_level=1 sweetkeys=0 sweettouch=0 sweetusb=0"
# TCG vCPUs are host threads that can be descheduled while holding a spinlock: the
# default lock timeout (3e6 ticks = 48 ms at 62.5 MHz) then panics. Scale every XNU
# machine_timeout x100 and drop the interrupt-masked/preemption debug panics.
BASE="$BASE ml-timeout-global-scale=100 interrupt-masked-debug-mode=0 sched_preemption_disable_debug_mode=0"
ADT=${ADT:-virt.adt}          # ADT=virt-hi.adt: kernel RAM 0x1_00000000 + 3.95 GiB (QEMU -m 8G)
python3 "$V/../refs/ios-sweet/tools/mkpayload.py" kernel-0081 "$ADT" "$OUT" --cmdline "$BASE $*" > /dev/null
echo "$OUT ($ADT): $BASE $*"
