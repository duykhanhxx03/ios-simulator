#!/usr/bin/env python3
"""Wrap a root filesystem image in a 4 KiB-sector GPT disk for sweet-ufshc.

  mkdisk.py ROOT.img DISK.img [--name userdata] [--tail-mib 8]

Layout (4096-byte LBAs, as the UFS LUN reports them):
  LBA 0      protective MBR
  LBA 1      GPT header, LBA 2.. entries (128 x 128 bytes)
  LBA 256    partition "userdata" = ROOT.img + tail (kernel log ring at its end)
  last LBAs  backup entries + header
The root image is copied sparsely (all-zero 1 MiB chunks are skipped).
"""
import argparse, os, struct, uuid, zlib

LBA = 4096
FIRST = 256                     # 1 MiB
NENT, ESZ = 128, 128
ENT_LBAS = NENT * ESZ // LBA    # 4
# Apple HFS+ partition type
HFS_TYPE = uuid.UUID('48465300-0000-11aa-aa11-00306543ecac')


def header(cur, backup, first, last, disk_guid, ent_lba, ent_crc):
    h = struct.pack('<8sIIIIQQQQ16sQIII', b'EFI PART', 0x00010000, 92, 0, 0,
                    cur, backup, first, last, disk_guid.bytes_le, ent_lba, NENT, ESZ, ent_crc)
    h = h[:16] + struct.pack('<I', zlib.crc32(h)) + h[20:]
    return h.ljust(LBA, b'\0')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root'); ap.add_argument('disk')
    ap.add_argument('--name', default='userdata')
    ap.add_argument('--tail-mib', type=int, default=8)
    a = ap.parse_args()
    if os.path.exists(a.disk):
        raise SystemExit(f'refusing to overwrite {a.disk}')

    rsize = os.path.getsize(a.root)
    part_lbas = (rsize + LBA - 1) // LBA + a.tail_mib * (1 << 20) // LBA
    p_first, p_last = FIRST, FIRST + part_lbas - 1
    total = p_last + 1 + ENT_LBAS + 1 + 255        # backup + slack
    last_usable = total - ENT_LBAS - 2

    ent = struct.pack('<16s16sQQQ72s', HFS_TYPE.bytes_le, uuid.uuid4().bytes_le,
                      p_first, p_last, 0, a.name.encode('utf-16-le'))
    ents = ent.ljust(NENT * ESZ, b'\0')
    ecrc = zlib.crc32(ents)
    dguid = uuid.uuid4()

    # protective MBR
    mbr = bytearray(LBA)
    mbr[446:462] = struct.pack('<B3sB3sII', 0, b'\0\x02\0', 0xee, b'\xff\xff\xff', 1,
                               min(total - 1, 0xffffffff))
    mbr[510:512] = b'\x55\xaa'

    with open(a.disk, 'wb') as d:
        d.truncate(total * LBA)
        d.write(mbr)
        d.write(header(1, total - 1, FIRST, last_usable, dguid, 2, ecrc))
        d.write(ents)
        d.seek((total - 1 - ENT_LBAS) * LBA); d.write(ents)
        d.write(header(total - 1, 1, FIRST, last_usable, dguid, total - 1 - ENT_LBAS, ecrc))
        # sparse copy of the root image
        chunk, zero = 1 << 20, bytes(1 << 20)
        with open(a.root, 'rb') as r:
            off = 0
            while True:
                b = r.read(chunk)
                if not b:
                    break
                if b != zero[:len(b)]:
                    d.seek(p_first * LBA + off); d.write(b)
                off += len(b)
    print(f'{a.disk}: {total * LBA >> 20} MiB, "{a.name}" LBA {p_first}-{p_last} '
          f'({part_lbas * LBA >> 20} MiB, root {rsize >> 20} MiB)')


if __name__ == '__main__':
    main()
