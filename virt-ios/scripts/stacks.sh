#!/bin/bash
# Symbolize the latest ios-sweet sampler stacks (SMP lines) of a running VM.
#   stacks.sh RUNDIR [PROC...]      (default procs: SpringBoard backboardd)
set -uo pipefail
V="/mnt/d/ios simulator/virt-ios"
cd /root/vios
RUN=$1; shift
PROCS=("${@:-SpringBoard backboardd}")
DSC=/mnt/fw/dsc/dyld_shared_cache_arm64
mountpoint -q /mnt/sysos || apfs-fuse -o allow_other dmglinks/sysos.dmg /mnt/sysos
python3 "$V/tools/klog.py" "$RUN" kernel-0081 --tail 100000 > "$RUN/klog.txt" || exit 1
for P in ${PROCS[@]}; do
	hdr=$(grep -a "SMP $P\[" "$RUN/klog.txt" | tail -1)
	[[ -n $hdr ]] || { echo "no samples for $P"; continue; }
	slide=$(sed -E 's/.*slide (0x[0-9a-f]+).*/\1/' <<<"$hdr")
	echo "=== $hdr"
	# the thread lines that follow the latest header
	awk -v h="$hdr" 'index($0,h){f=1;next} f && /SMP [A-Za-z]+\[/{exit} f' "$RUN/klog.txt" |
	grep -a " $P t[0-9]* pc" | while read -r line; do
		t=$(sed -E 's/.* ('"$P"' t[0-9]+) .*/\1/' <<<"$line")
		addrs=$(sed -E 's/.* pc (0x[0-9a-f]+) lr (0x[0-9a-f]+) sp 0x[0-9a-f]+ bt ?//; s/.* pc (0x[0-9a-f]+).*/\1/' <<<"$line")
		pc=$(sed -E 's/.* pc (0x[0-9a-f]+).*/\1/' <<<"$line")
		echo "--- $t"
		for a in $pc $addrs; do
			[[ $a == 0x0 ]] && continue
			printf '  %s  ' "$a"
			ipsw dyld a2s "$DSC" --slide "$slide" "$a" 2>/dev/null | tail -1
		done
	done
done
