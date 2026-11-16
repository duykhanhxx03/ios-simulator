#!/usr/bin/env python3
"""Apple Device Tree for virt-ios (QEMU virt,sweet=on).

  mkadt_virt.py OUT.adt --d22ap D22AP.(adt|im4p) [--highmem] [--dump]

Builds ios-sweet's QEMU tree (tools/mkadt.py --qemu: virt GIC/timer, one
CPU, iPhone X /product identity) and adds the /sweet-ufs node pointing at
the sweet-ufshc device that virt-ios maps at 0x0b000000 (on the phone the
UFS host is at 0x01d84000, which on virt is flash).
"""
import argparse, os, sys

SWEET_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '../../refs/ios-sweet/tools')
sys.path.insert(0, os.path.abspath(SWEET_TOOLS))
import adt     # noqa: E402  (ios-sweet, MIT)
import mkadt   # noqa: E402  (ios-sweet, MIT)

VIRT_UFS, VIRT_UFS_SIZE = 0x0b000000, 0x3000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--d22ap', required=True, help='iPhone X DeviceTree (.im4p from the IPSW or raw .adt)')
    ap.add_argument('--highmem', action='store_true')
    ap.add_argument('--cpus', type=int, default=1,
                    help='CPU nodes (MPIDR = core << 8 like the phone; QEMU virt,sweet=on matches)')
    ap.add_argument('--dump', action='store_true')
    a = ap.parse_args()

    raw = adt.load(a.d22ap)            # im4p -> raw ADT (lzfse if needed)
    d22 = a.out + '.d22ap.adt'
    open(d22, 'wb').write(raw)
    orig = mkadt.d22ap_product
    mkadt.d22ap_product = lambda path=None: orig(d22)

    root = mkadt.build(qemu=True, highmem=a.highmem)
    used = [int.from_bytes(v, 'little') for n in walk(root) for k, f, v in n.props if k == 'AAPL,phandle']
    if a.cpus > 1:
        add_cpus(root, a.cpus, max(used) + 1)
        used = [int.from_bytes(v, 'little') for n in walk(root) for k, f, v in n.props if k == 'AAPL,phandle']
    root.children.append(mkadt.node('sweet-ufs', [
        ('AAPL,phandle', mkadt.u32(max(used) + 1)),
        ('compatible', mkadt.cstr('qcom,ufshc')),
        ('reg', mkadt.u64(VIRT_UFS, VIRT_UFS_SIZE)),
    ]))
    blob = adt.serialize(root)
    open(a.out, 'wb').write(blob)
    os.remove(d22)
    if a.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'{a.out}: {len(blob)} bytes', file=sys.stderr)


def add_cpus(root, n, next_phandle):
    """Clone /cpus/cpu0 into cpu1..cpu(n-1), as mkadt.py does for the phone."""
    cpus = next(c for c in root.children if c.name == 'cpus')
    cpu0 = cpus.children[0]
    cpu0.props = [(k, f, mkadt.u32(0) if k == 'reg' else v) for k, f, v in cpu0.props]
    for core in range(1, n):
        c = adt.Node()
        repl = {
            'name': mkadt.cstr(f'cpu{core}'),
            'AAPL,phandle': mkadt.u32(next_phandle + core - 1),
            'reg': mkadt.u32(core << 8),
            'cpu-id': mkadt.u32(core),
            'cluster-core-id': mkadt.u32(core),
            'l2-cache-id': mkadt.u32(core),
            'state': mkadt.cstr('stopped'),
        }
        c.props = [(k, f, repl.get(k, v)) for k, f, v in cpu0.props]
        c.children = []
        cpus.children.append(c)


def walk(n):
    yield n
    for c in n.children:
        yield from walk(c)


if __name__ == '__main__':
    main()
