#!/usr/bin/env python3
"""Snapshot the framebuffer of a running virt-ios VM (does not stop it).

  snap.py RUNDIR OUT.png
"""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    'qemu_sweet', os.path.join(HERE, '../../refs/ios-sweet/tools/qemu-sweet.py'))
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)

run, out = os.path.abspath(sys.argv[1]), sys.argv[2]
raw = f'{run}/snap.raw'
qs.qmp(f'{run}/mon.sock', pmemsave=(qs.FB_BASE, qs.FB_STRIDE * qs.FB_H, raw))
qs.write_png(out, open(raw, 'rb').read())
print(out)
