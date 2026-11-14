#!/usr/bin/env python3
"""Copy a sparse file's data into an existing destination without writing its holes.

    sparse-copy.py <src> <dst> [--chunk MiB]

Run inside WSL by copy-vm-from-wsl.ps1. <dst> must already exist with the source's length and,
on NTFS, the sparse attribute (the PowerShell side does `fsutil sparse setflag` + SetLength):
drvfs (/mnt/<drive>, 9p) has no way to set the attribute itself, and a plain `cp --sparse=always`
onto drvfs leaves a fully allocated file. The destination is opened without O_TRUNC, so the
attribute survives; only the source's data extents (SEEK_DATA/SEEK_HOLE) are read, and inside
them, chunks that are all zeros are skipped too, as cp --sparse=always does.
Prints one JSON line: bytes written, chunks skipped, extents.
"""
import json
import os
import sys

def main():
    args = sys.argv[1:]
    chunk = 4 << 20
    if "--chunk" in args:
        i = args.index("--chunk")
        chunk = int(args[i + 1]) << 20
        del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    src, dst = args
    sfd = os.open(src, os.O_RDONLY)
    size = os.fstat(sfd).st_size
    dfd = os.open(dst, os.O_WRONLY)  # no O_CREAT/O_TRUNC: keep NTFS's sparse attribute
    if os.fstat(dfd).st_size != size:
        sys.exit(f"sparse-copy: {dst} is {os.fstat(dfd).st_size} bytes, the source {size}: "
                 "create it with the source's length first")
    zero = bytes(chunk)
    written = skipped = extents = 0
    off = 0
    last_report = 0
    while off < size:
        try:
            data = os.lseek(sfd, off, os.SEEK_DATA)
        except OSError:  # ENXIO: no data past off
            break
        hole = os.lseek(sfd, data, os.SEEK_HOLE)
        extents += 1
        pos = data
        while pos < hole:
            n = min(chunk, hole - pos)
            buf = os.pread(sfd, n, pos)
            if len(buf) != n:
                sys.exit(f"sparse-copy: short read at {pos}")
            if buf == zero[:n]:
                skipped += 1
            else:
                mv = memoryview(buf)
                done = 0
                while done < n:
                    done += os.pwrite(dfd, mv[done:], pos + done)
                written += n
            pos += n
            if written - last_report >= (1 << 30):
                last_report = written
                print(f"  {written >> 20} MiB written, at {pos * 100 // size}% of the image",
                      file=sys.stderr, flush=True)
        off = hole
    os.fsync(dfd)
    os.close(dfd)
    os.close(sfd)
    print(json.dumps({"size": size, "written": written, "zero_chunks_skipped": skipped,
                      "extents": extents}))

if __name__ == "__main__":
    main()
