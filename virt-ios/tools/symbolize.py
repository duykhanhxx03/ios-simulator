#!/usr/bin/env python3
"""Map kernel addresses to symbol+offset using llvm-nm.

  symbolize.py KERNEL ADDR [ADDR ...]     (hex, unslid as printed in panics)
"""
import bisect, subprocess, sys


def main():
    kern, addrs = sys.argv[1], [int(a, 16) for a in sys.argv[2:]]
    syms = []
    for line in subprocess.check_output(['llvm-nm', '-n', '--defined-only', kern], text=True).splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] in 'tTsSdD':
            syms.append((int(parts[0], 16), parts[2]))
    syms.sort()
    keys = [s[0] for s in syms]
    for a in addrs:
        i = bisect.bisect_right(keys, a) - 1
        print(f'{a:#x}  {syms[i][1]}+{a - syms[i][0]:#x}' if i >= 0 else f'{a:#x}  ?')


if __name__ == '__main__':
    main()
