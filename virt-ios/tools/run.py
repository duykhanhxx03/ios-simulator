#!/usr/bin/env python3
"""Boot a SWEETXNU payload on QEMU virt with the virt-ios devices.

  run.py PAYLOAD OUTDIR [--disk DISK.img] [--display gtk|sdl|none]
         [--secs N] [--gdb] [--qemu PATH] [--smp N]

Loads the payload exactly like ios-sweet's tools/qemu-sweet.py (kernel
segments, ADT and boot_args placed at their physical addresses, a jump stub
at 0x40200000) and adds  -M virt,sweet=on  with the disk as drive "ufs0".
Serial goes to OUTDIR/serial.txt (and the terminal with --display none).
"""
import argparse, importlib.util, os, struct, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
QS = os.path.abspath(os.path.join(HERE, '../../refs/ios-sweet/tools/qemu-sweet.py'))
spec = importlib.util.spec_from_file_location('qemu_sweet', QS)
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)     # ios-sweet (MIT): build_image(), stub()

DEFAULT_QEMU = '/opt/src/qemu/build/qemu-system-aarch64'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('payload'); ap.add_argument('outdir')
    ap.add_argument('--disk')
    ap.add_argument('--display', default='gtk')
    ap.add_argument('--secs', type=float, default=0, help='kill after N seconds (0 = run until exit)')
    ap.add_argument('--gdb', action='store_true')
    ap.add_argument('--smp', type=int, default=1)
    ap.add_argument('--cpu', default='cortex-a76')
    ap.add_argument('--qemu', default=DEFAULT_QEMU)
    ap.add_argument('--trace', action='store_true', help='-d int,guest_errors,unimp to qemu.log')
    a = ap.parse_args()

    payload = open(a.payload, 'rb').read()
    out = os.path.abspath(a.outdir)
    os.makedirs(out, exist_ok=True)
    phys_base, img, ba_pa, entry_pa = qs.build_image(payload)
    open(f'{out}/ram.bin', 'wb').write(img)
    open(f'{out}/stub.bin', 'wb').write(qs.stub(ba_pa, entry_pa))
    mem_size = struct.unpack_from('<Q', payload, 0x40)[0]
    ram_gib = max(4, -(-(phys_base + mem_size - 0x40000000) // (1 << 30)))
    print(f'phys_base={phys_base:#x} mem={mem_size >> 20}MiB boot_args={ba_pa:#x} '
          f'entry={entry_pa:#x} -> -m {ram_gib}G', file=sys.stderr)

    machine = 'virt,gic-version=3' + (',sweet=on' if a.disk else '')
    cmd = [a.qemu, '-M', machine, '-cpu', a.cpu, '-smp', str(a.smp), '-m', f'{ram_gib}G',
           '-no-reboot', '-qmp', f'unix:{out}/mon.sock,server,nowait',
           '-device', f'loader,file={out}/ram.bin,addr={phys_base:#x},force-raw=on',
           '-device', f'loader,file={out}/stub.bin,addr={qs.STUB_PA:#x},force-raw=on',
           '-device', f'loader,addr={qs.STUB_PA:#x},cpu-num=0']
    if a.disk:
        cmd += ['-drive', f'if=none,id=ufs0,file={os.path.abspath(a.disk)},format=raw,cache=writeback']
    if a.display == 'none':
        cmd += ['-nographic', '-serial', f'mon:stdio']
    else:
        cmd += ['-display', a.display, '-serial', f'file:{out}/serial.txt']
    if a.trace:
        cmd += ['-d', 'int,guest_errors,unimp', '-D', f'{out}/qemu.log']
    if a.gdb:
        cmd += ['-S', '-gdb', 'tcp::1234']
    print(' '.join(cmd), file=sys.stderr)
    if os.path.exists(f'{out}/mon.sock'):
        os.unlink(f'{out}/mon.sock')

    p = subprocess.Popen(cmd)
    t0 = time.time()
    try:
        while p.poll() is None:
            if a.secs and time.time() - t0 > a.secs:
                try:
                    qs.qmp(f'{out}/mon.sock', 'stop', pmemsave=(qs.FB_BASE, qs.FB_STRIDE * qs.FB_H, f'{out}/fb.raw'))
                    qs.write_png(f'{out}/fb.png', open(f'{out}/fb.raw', 'rb').read())
                except Exception as e:
                    print(f'snapshot failed: {e}', file=sys.stderr)
                p.kill()
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        p.kill()
    p.wait()
    return p.returncode or 0


if __name__ == '__main__':
    sys.exit(main())
