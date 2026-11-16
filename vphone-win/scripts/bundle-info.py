#!/usr/bin/env python3
"""Describe what a vphone VM bundle needs to boot, for copy-vm-from-wsl.ps1 (runs inside WSL).

    bundle-info.py <vm-dir>

Prints JSON: the files run-vphone.sh / run-vphone.ps1 read (with apparent and allocated sizes),
and the processes that have the bundle open (an open fd under <vm-dir>, or a qemu-system-*
whose command line names it). Run as root to see every process.

The boot reads: ecid, config.plist (romImages), the two ROMs, nvram.bin, Disk.img, SEPStorage,
and optionally serial and gfx-res. The restore tree (*_Restore), FirmwareOriginals, the patch
plists, .vphoned.signed and the run logs are create-vm.sh's working files, not boot inputs.
"""
import json
import os
import plistlib
import sys

def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    vm = os.path.realpath(sys.argv[1])
    if not os.path.isdir(vm):
        sys.exit(f"bundle-info: {vm} is not a directory")
    cfg = {}
    try:
        with open(os.path.join(vm, "config.plist"), "rb") as f:
            cfg = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        pass
    roms = cfg.get("romImages") or {}
    required = ["config.plist", "ecid",
                roms.get("avpBooter") or "AVPBooter.vresearch1.bin",
                roms.get("avpSEPBooter") or "AVPSEPBooter.vresearch1.bin",
                "nvram.bin", "Disk.img"]
    # run-vphone.sh hard-codes these names; config.plist's own keys should agree
    for key, name in (("diskImage", "Disk.img"), ("nvramStorage", "nvram.bin"),
                      ("sepStorage", "SEPStorage")):
        if cfg.get(key) not in (None, name):
            print(f"bundle-info: config.plist {key}={cfg.get(key)!r}, the launchers read {name}",
                  file=sys.stderr)
    # SEPStorage: attached when present; the rest: small per-machine settings
    optional = ["SEPStorage", "serial", "gfx-res", "gfx-res.3x", "cid"]
    files = []
    for name in required + optional:
        p = os.path.join(vm, name)
        st = os.stat(p) if os.path.isfile(p) else None
        files.append({"name": name, "required": name in required, "exists": st is not None,
                      "size": st.st_size if st else 0,
                      "allocated": st.st_blocks * 512 if st else 0})

    holders = []
    prefix = vm + os.sep
    for pid in filter(str.isdigit, os.listdir("/proc")):
        if int(pid) == os.getpid():
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip()
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        why = []
        if comm.startswith("qemu-system") and vm in cmd:
            why.append("qemu command line names the bundle")
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    t = os.readlink(f"/proc/{pid}/fd/{fd}")
                except OSError:
                    continue
                if t.startswith(prefix):
                    why.append("has " + os.path.basename(t) + " open")
                    break
        except OSError:
            pass
        if why:
            holders.append({"pid": int(pid), "comm": comm, "why": why, "cmd": cmd[:200]})
    print(json.dumps({"vm": vm, "files": files, "holders": holders}))

if __name__ == "__main__":
    main()
