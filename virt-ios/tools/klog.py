#!/usr/bin/env python3
"""Dump the XNU kernel message buffer (msgbuf) of a running virt-ios VM.

  klog.py RUNDIR KERNEL [--tail N]

Reads _msgbufp through QEMU's HMP memsave (virtual addresses, the guest
CPU's MMU does the translation), then the ring msg_bufc[msg_size] and
prints it in order starting at msg_bufx. The VM keeps running.
"""
import argparse, importlib.util, os, struct, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    'qemu_sweet', os.path.join(HERE, '../../refs/ios-sweet/tools/qemu-sweet.py'))
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)


def sym(kernel, name):
    for line in subprocess.check_output(['llvm-nm', kernel], text=True).splitlines():
        p = line.split()
        if len(p) == 3 and p[2] == name:
            return int(p[0], 16)
    raise SystemExit(f'{name} not in {kernel}')


def vread(mon, va, size, tmp):
    if os.path.exists(tmp):
        os.unlink(tmp)
    r = qs.qmp(mon, f'memsave {va:#x} {size} "{tmp}"')[0]
    if r.strip():
        raise SystemExit(f'memsave {va:#x}: {r.strip()}')
    return open(tmp, 'rb').read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run'); ap.add_argument('kernel')
    ap.add_argument('--tail', type=int, default=80)
    a = ap.parse_args()
    run = os.path.abspath(a.run)
    mon, tmp = f'{run}/mon.sock', f'{run}/klogtmp.bin'
    msgbufp = struct.unpack('<Q', vread(mon, sym(a.kernel, '_msgbufp'), 8, tmp))[0]
    magic, size, bufx, bufr, bufc = struct.unpack('<4iQ', vread(mon, msgbufp, 24, tmp))
    if magic != 0x63061:
        raise SystemExit(f'bad msgbuf magic {magic:#x} at {msgbufp:#x}')
    ring = vread(mon, bufc, size, tmp)
    text = (ring[bufx:] + ring[:bufx]).replace(b'\0', b'').decode('utf-8', 'replace')
    lines = text.splitlines()
    print('\n'.join(lines[-a.tail:]))


if __name__ == '__main__':
    main()
