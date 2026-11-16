#!/usr/bin/env python3
"""winbench.py: upstream's tools/scroll-measure.sh for native Windows.

    python winbench.py --qmp 4445 --log F:\\vpw\\runs\\win-bootN.out [--pid PID]

The VM must run with REIMS_VGPU_STATS=1 (per-second "reims-vgpu stats" lines on QEMU's stderr,
which run-vphone.ps1's caller redirects into --log) and `-qmp tcp:127.0.0.1:PORT,server=on,wait=off`.
Like upstream: Home, open Settings (retrying the tap until the screen changes), two warm-up
scrolls, then 6 up/down swipes over ~15 s; prints the guest frames per second, its average, the
worst frame gap and QEMU's host CPU over the measured window.

Coordinates are upstream's: the 3x display (1290x2796) mapped onto the absolute axis, so they
hold at any resolution. Settings is the 4th icon of the home screen's 4th row there.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time

W, H = 1290, 2796


class Qmp:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.f = self.s.makefile("rw", encoding="utf-8", newline="\n")
        self.f.readline()
        self.cmd("qmp_capabilities")

    def cmd(self, name, **args):
        msg = {"execute": name}
        if args:
            msg["arguments"] = args
        self.f.write(json.dumps(msg) + "\n")
        self.f.flush()
        while True:
            r = json.loads(self.f.readline())
            if "return" in r or "error" in r:
                return r

    def events(self, *events):
        return self.cmd("input-send-event", events=list(events))


def at(x, y):
    return [
        {"type": "abs", "data": {"axis": "x", "value": int(x / W * 32767)}},
        {"type": "abs", "data": {"axis": "y", "value": int(y / H * 32767)}},
    ]


def btn(down, button="left"):
    return {"type": "btn", "data": {"down": down, "button": button}}


def tap(q, x, y):
    q.events(*at(x, y))
    time.sleep(0.05)
    q.events(btn(True))
    time.sleep(0.12)
    q.events(btn(False))


def swipe(q, x0, y0, x1, y1, ms=250, steps=15):
    q.events(*at(x0, y0))
    time.sleep(0.05)
    q.events(btn(True))
    for i in range(1, steps + 1):
        t = i / steps
        q.events(*at(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        time.sleep(ms / 1000 / steps)
    q.events(btn(False))


def home(q):
    # A middle click: usb-touchscreen turns it into Cmd+H, iOS's Home shortcut (upstream qmp.py).
    q.events(btn(True, "middle"))
    time.sleep(0.12)
    q.events(btn(False, "middle"))


def screen(q, path):
    """A small grayscale signature of the screen (PPM: no PNG compression under the BQL)."""
    r = q.cmd("screendump", filename=path, format="ppm")
    if "error" in r:
        return None
    with open(path, "rb") as f:
        data = f.read()
    # P6 header: magic, width height, maxval, then RGB bytes.
    parts = data.split(b"\n", 3)
    w, h = map(int, parts[1].split())
    px = parts[3]
    sig = []
    for gy in range(0, h, max(1, h // 48)):
        for gx in range(0, w, max(1, w // 24)):
            i = (gy * w + gx) * 3
            sig.append(sum(px[i:i + 3]) // 3)
    return sig


def changed(a, b):
    if a is None or b is None or len(a) != len(b):
        return True
    return sum(abs(x - y) > 30 for x, y in zip(a, b)) > len(a) // 20


def unlock(port, tries=6):
    """vphoned's screen.unlock through the vsock device's host side (CONNECT 1339). True: unlocked."""
    if not port:
        return False
    body = json.dumps({"method": "screen.unlock", "params": {}, "id": 1}).encode()
    for _ in range(tries):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=60) as s:
                s.sendall(b"CONNECT 1339\n")
                f = s.makefile("rb")
                if not f.readline().startswith(b"OK"):
                    raise OSError("vsock CONNECT refused")
                s.sendall(b"POST /v1/rpc HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
                          b"Connection: close\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
                reply = f.read()
            if b'"locked":false' in reply:
                return True
        except OSError:
            pass
        time.sleep(10)
    return False


def cpu_seconds(pid):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {pid}).TotalProcessorTime.TotalSeconds"],
        capture_output=True, text=True).stdout.strip()
    return float(out.replace(",", "."))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qmp", type=int, default=4445)
    ap.add_argument("--log", required=True, help="QEMU's redirected stdout/stderr")
    ap.add_argument("--pid", type=int, help="QEMU's pid (default: the only qemu-system-aarch64)")
    ap.add_argument("--tmp", default=os.environ.get("TEMP", "."))
    ap.add_argument("--vsock", type=int, default=13390, help="run-vphone.ps1's -VsockPort (0: none)")
    ap.add_argument("--rounds", type=int, default=3, help="measured rounds, averaged")
    a = ap.parse_args()
    pid = a.pid or int(subprocess.run(
        ["powershell", "-NoProfile", "-Command", "(Get-Process qemu-system-aarch64).Id"],
        capture_output=True, text=True).stdout.split()[0])

    q = Qmp(a.qmp)
    # After a boot the phone is locked (no passcode on these VMs): vphoned's screen.unlock over
    # the vsock device when there is one, else a swipe up from the bottom edge.
    if not unlock(a.vsock):
        swipe(q, 645, 2760, 645, 1200, 300, 20)
    time.sleep(4)
    home(q)
    time.sleep(4)
    shot = os.path.join(a.tmp, "winbench-screen.ppm")
    opened = False
    for _ in range(3):
        before = screen(q, shot)
        tap(q, 1089, 1953)
        time.sleep(8)
        if changed(before, screen(q, shot)):
            opened = True
            break
    if not opened:
        sys.exit("Settings did not open")
    for _ in range(2):
        swipe(q, 645, 2200, 645, 900)
        time.sleep(1.5)
        swipe(q, 645, 900, 645, 2200)
        time.sleep(1.5)
    time.sleep(3)

    # Rounds of upstream's measurement; one round alone varied by about +-3 fps here.
    averages, cpus, gaps = [], [], []
    for r in range(a.rounds):
        with open(a.log, "rb") as f:
            f.seek(0, 2)
            start = f.tell()
        c0, t0 = cpu_seconds(pid), time.time()
        for _ in range(6):
            swipe(q, 645, 2200, 645, 900)
            time.sleep(0.8)
            swipe(q, 645, 900, 645, 2200)
            time.sleep(0.8)
        time.sleep(2)
        c1, t1 = cpu_seconds(pid), time.time()

        with open(a.log, "rb") as f:
            f.seek(start)
            # Windows PowerShell's `*>` writes UTF-16: drop its NULs so the text matches either way.
            tail = f.read().replace(b"\x00", b"").decode("utf-8", "replace")
        rates = []
        for m in re.finditer(r"reims-vgpu stats.*?guest ([0-9.]+)/s, max gap ([0-9.]+) ms", tail):
            fps, gap = float(m.group(1)), float(m.group(2))
            if fps > 0:
                rates.append((fps, gap))
        if not rates:
            sys.exit("no 'reims-vgpu stats' lines in the log: was REIMS_VGPU_STATS=1 set?")
        avg = sum(f for f, _ in rates) / len(rates)
        # The first second holds the round's start (the screen was idle before it): its gap is
        # the wait for the first swipe, not a stall, so the worst gap skips it.
        worst = int(max((g for _, g in rates[1:]), default=0))
        averages.append(avg)
        cpus.append((c1 - c0) / (t1 - t0))
        gaps.append(worst)
        print(f"round {r + 1}: " + " ".join(f"{f:g}/{int(g)}" for f, g in rates))
        print(f"  frames/s avg {avg:.1f}, worst gap {worst} ms, QEMU host CPU {cpus[-1]:.2f} cores")
        time.sleep(3)
    print(f"RESULT frames/s {sum(averages) / len(averages):.1f} "
          f"(rounds {' '.join(f'{x:.1f}' for x in averages)}), "
          f"worst gap median {sorted(gaps)[len(gaps) // 2]} ms, "
          f"QEMU CPU {sum(cpus) / len(cpus):.2f} cores")


if __name__ == "__main__":
    main()
