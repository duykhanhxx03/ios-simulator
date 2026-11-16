#!/bin/bash
# Edit a preference plist inside the (stopped) virt-ios disk image.
#   set-pref.sh DISK DOMAIN KEY TYPE VALUE      (TYPE: int|bool|str)
# Domain file: /private/var/mobile/Library/Preferences/DOMAIN.plist (mobile, 501:501 0600)
set -euo pipefail
DISK=$1 DOMAIN=$2 KEY=$3 TYPE=$4 VAL=$5
pgrep -x qemu-system-aar >/dev/null && { echo "VM is running: stop it first"; exit 1; }
MNT=/mnt/iosroot
mkdir -p $MNT
# userdata partition = LBA 256 (4 KiB), HFS+ volume 16 GiB (mkdisk.py layout)
LOOP=$(losetup -f --show -o $((256 * 4096)) --sizelimit $((16384 << 20)) "$DISK")
trap 'umount $MNT 2>/dev/null; losetup -d $LOOP' EXIT
# A VM that was killed leaves the volume dirty: Linux then mounts it read-only.
fsck.hfsplus -f -y $LOOP | tail -2 || true
mount -t hfsplus -o force $LOOP $MNT
F=$MNT/private/var/mobile/Library/Preferences/$DOMAIN.plist
python3 - "$F" "$KEY" "$TYPE" "$VAL" <<'EOF'
import os, plistlib, sys
f, key, typ, val = sys.argv[1:]
d = plistlib.load(open(f, 'rb')) if os.path.exists(f) else {}
d[key] = {'int': int, 'bool': lambda v: v.lower() in ('1', 'true', 'yes'), 'str': str}[typ](val)
plistlib.dump(d, open(f, 'wb'), fmt=plistlib.FMT_BINARY)
print(f'{os.path.basename(f)}: {key} = {d[key]!r}')
EOF
chown 501:501 "$F"; chmod 0600 "$F"
sync
