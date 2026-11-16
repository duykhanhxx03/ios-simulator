#!/bin/bash
# Build the iOS 16.7.16 (iPhone X, 20H392) root for virt-ios:
#   1. ios-sweet mkiosroot.sh  : OS + SystemOS + AppOS cryptexes -> one HFS+ image
#   2. ios-sweet sweet-root-tweaks.py : launchd/Setup/activation/... bring-up changes
#   3. virt-ios mkdisk.py      : wrap it in a 4K-sector GPT disk ("userdata")
set -euo pipefail
V="/mnt/d/ios simulator/virt-ios"
T="$V/../refs/ios-sweet/tools"
I=/mnt/d/ios-work/ipsw
W=/root/vios
cd $W

if [[ ! -e ios16-root.img ]]; then
	# the Windows checkout has CRLF line endings: run an LF copy
	sed 's/\r$//' "$T/mkiosroot.sh" > $W/mkiosroot.sh
	rm -f ios16-root.img.tmp
	grep -q hfsplus /proc/filesystems || modprobe hfsplus
	bash $W/mkiosroot.sh $I/087-86698-044.dmg $I/087-86593-047.dmg $I/087-86715-047.dmg ios16-root.img.tmp 16G
	mv ios16-root.img.tmp ios16-root.img
fi

mkdir -p mnt
fsck.hfsplus -f -y ios16-root.img > /dev/null || true
mount -t hfsplus -o loop,force ios16-root.img mnt
trap 'umount mnt 2>/dev/null || true' EXIT
python3 "$T/sweet-root-tweaks.py" $W/mnt
sync
umount mnt
trap - EXIT
fsck.hfsplus -f -n ios16-root.img | tail -2

rm -f ios16-disk.img
python3 "$V/tools/mkdisk.py" ios16-root.img ios16-disk.img
rm -f ios16-root.img
ls -lsh $W
df -h / | tail -1
