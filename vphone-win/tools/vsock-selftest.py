#!/usr/bin/env python3
"""End-to-end test of virtio-vsock-sock-pci without a guest OS.

QEMU runs -M virt -S -gdb (qtest is POSIX-only); this script is the "guest": through the
gdbstub (RAM via m/M packets, MMIO by single-stepping one ldr/str on the halted vCPU) it
plays a virtio-pci modern driver (ECAM config, BAR4 caps, split rings in guest RAM, polling
the used rings), and a guest vsock stack listening on port 1339 that echoes what it reads.
The guest advertises a small buf_alloc and sends credit updates only when asked, like iOS
(worst case), so the host->guest stream depends on the device's CREDIT_REQUESTs.
A host thread connects to the device's TCP port with the Firecracker protocol.

  python vsock-selftest.py        (env: ECHO_BYTES, GUEST_BUF_ALLOC, DEV_BUF_ALLOC,
                                   QTEST_PORT = gdb port, VSOCK_PORT; prints PASS)
"""
import base64, os, socket, struct, subprocess, sys, threading, time

QEMU = r"D:\vpw\build\qemu-pvg\qemu-system-aarch64.exe"
QTEST_PORT = int(os.environ.get("QTEST_PORT", "15571"))
VSOCK_PORT = int(os.environ.get("VSOCK_PORT", "15572"))
ECAM = 0x3f000000
DEV = 5
BAR4 = 0x10000000
RAM = 0x40000000
QSZ = 256
GUEST_CID = 3
GUEST_BUF_ALLOC = int(os.environ.get("GUEST_BUF_ALLOC", str(128 * 1024)))
RXBUF = 4096
HDR = struct.Struct("<QQIIIHHIII")  # 44 bytes
OP_REQUEST, OP_RESPONSE, OP_RST, OP_SHUTDOWN, OP_RW, OP_CREDIT_UPDATE, OP_CREDIT_REQUEST = range(1, 8)
OPN = {1: "REQUEST", 2: "RESPONSE", 3: "RST", 4: "SHUTDOWN", 5: "RW", 6: "CREDIT_UPDATE", 7: "CREDIT_REQUEST"}


STUB = 0x40000000  # code page in guest RAM; rings etc. start at RAM + 1 MiB
STORE = {"b": 0x39000001, "w": 0x79000001, "l": 0xb9000001, "q": 0xf9000001}  # str* w1/x1,[x0]
LOAD = {"b": 0x39400001, "w": 0x79400001, "l": 0xb9400001, "q": 0xf9400001}   # ldr* w1/x1,[x0]
NOP = 0xd503201f


class GdbGuest:
    """The 'guest CPU' through QEMU's gdbstub: RAM via m/M packets, MMIO by running one
    load/store instruction on the (otherwise halted) vCPU up to a breakpoint."""

    def __init__(self, sock):
        self.s = sock
        self.buf = b""
        self.ack = True
        self.cmd("QStartNoAckMode")
        self.ack = False
        code = b""
        self.slot = {}
        for i, (k, insn) in enumerate(list(("S" + w, v) for w, v in STORE.items()) +
                                      list(("L" + w, v) for w, v in LOAD.items())):
            self.slot[k] = STUB + 8 * i
            code += struct.pack("<II", insn, NOP)
        self.mwrite(STUB, code)
        for k, a in self.slot.items():
            assert self.cmd("Z0,%x,4" % (a + 4)) == "OK"

    def _recv_packet(self):
        while True:
            i = self.buf.find(b"$")
            j = self.buf.find(b"#", i + 1) if i >= 0 else -1
            if i >= 0 and j >= 0 and len(self.buf) >= j + 3:
                data = self.buf[i + 1:j]
                self.buf = self.buf[j + 3:]
                if self.ack:
                    self.s.sendall(b"+")
                return data.decode()
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("gdbstub closed")
            self.buf += chunk

    def cmd(self, c):
        p = c.encode()
        self.s.sendall(b"$%s#%02x" % (p, sum(p) & 0xff))
        return self._recv_packet()

    def _reg(self, n, v):
        assert self.cmd("P%x=%s" % (n, struct.pack("<Q", v).hex())) == "OK"

    def _run(self, key, addr, val=0):
        self._reg(0, addr)
        self._reg(1, val)
        self._reg(32, self.slot[key])
        r = self.cmd("c")
        assert r.startswith("T05") or r.startswith("S05"), r
        return int.from_bytes(bytes.fromhex(self.cmd("p1")), "little")

    def rd(self, w, a): return self._run("L" + w, a)
    def wr(self, w, a, v): self._run("S" + w, a, v)

    def mread(self, a, n):
        out = b""
        while n:
            k = min(n, 1024)
            r = self.cmd("m%x,%x" % (a, k))
            out += bytes.fromhex(r)
            a += k; n -= k
        return out

    def mwrite(self, a, data):
        while data:
            k = data[:1024]
            assert self.cmd("M%x,%x:%s" % (a, len(k), k.hex())) == "OK"
            a += len(k); data = data[len(k):]


class Ring:
    def __init__(self, qt, idx, base, notify_addr):
        self.qt, self.idx, self.notify_addr = qt, idx, notify_addr
        self.desc = base
        self.avail = base + 16 * QSZ
        self.used = (self.avail + 6 + 2 * QSZ + 4095) & ~4095
        self.bufs = (self.used + 6 + 8 * QSZ + 4095) & ~4095
        self.avail_idx = 0
        self.last_used = 0
        self.free = list(range(QSZ))
        qt.mwrite(base, bytes(self.bufs - base))

    def add(self, buf_addr, length, write, data=b""):
        d = self.free.pop()
        if data:
            self.qt.mwrite(buf_addr, data)
        self.qt.mwrite(self.desc + 16 * d, struct.pack("<QIHH", buf_addr, length, 2 if write else 0, 0))
        self.qt.mwrite(self.avail + 4 + 2 * (self.avail_idx % QSZ), struct.pack("<H", d))
        self.avail_idx = (self.avail_idx + 1) & 0xffff
        self.qt.mwrite(self.avail + 2, struct.pack("<H", self.avail_idx))
        return d

    def kick(self):
        self.qt.wr("w", self.notify_addr, self.idx)

    def reap(self):
        out = []
        uidx = struct.unpack("<H", self.qt.mread(self.used + 2, 2))[0]
        while self.last_used != uidx:
            d, ln = struct.unpack("<II", self.qt.mread(self.used + 4 + 8 * (self.last_used % QSZ), 8))
            self.last_used = (self.last_used + 1) & 0xffff
            out.append((d, ln))
        return out


def cfg_addr(off): return ECAM + (DEV << 15) + off


def main():
    qemu = subprocess.Popen([QEMU, "-M", "virt,highmem=off", "-cpu", "cortex-a57", "-smp", "1",
                             "-accel", "tcg", "-S", "-gdb", "tcp:127.0.0.1:%d" % QTEST_PORT,
                             "-display", "none", "-nodefaults", "-m", "256M",
                             "-device", "virtio-vsock-sock-pci,addr=0x%x,guest-cid=%d,host-port=%d%s"
                             % (DEV, GUEST_CID, VSOCK_PORT,
                                ",buf-alloc=" + os.environ["DEV_BUF_ALLOC"]
                                if os.environ.get("DEV_BUF_ALLOC") else "")])
    try:
        for _ in range(100):
            try:
                qs = socket.create_connection(("127.0.0.1", QTEST_PORT))
                break
            except OSError:
                time.sleep(0.1)
        qt = GdbGuest(qs)
        run(qt)
    finally:
        qemu.kill()


def host_connect(port, timeout=5):
    s = socket.create_connection(("127.0.0.1", VSOCK_PORT), timeout=timeout)
    s.sendall(b"CONNECT %d\n" % port)
    return s


def run(qt):
    vid = qt.rd("l", cfg_addr(0))
    print("vendor/device 0x%08x" % vid)
    assert vid & 0xffff == 0x1af4 and vid >> 16 == 0x1053, hex(vid)

    # before DRIVER_OK: the host connection is closed
    s = host_connect(1339)
    try:
        r = s.recv(100)
    except ConnectionResetError:
        r = b"<reset>"
    print("no driver: CONNECT ->", r or b"<closed>")
    assert r in (b"", b"<reset>")
    s.close()

    qt.wr("l", cfg_addr(0x20), BAR4 | 0x4)  # 64-bit mem BAR
    qt.wr("l", cfg_addr(0x24), 0)
    qt.wr("w", cfg_addr(0x04), 0x6)
    caps = {}
    p = qt.rd("b", cfg_addr(0x34))
    while p:
        cid, nxt, _, ctype, bar = (qt.rd("b", cfg_addr(p + i)) for i in range(5))
        if cid == 0x09:
            off = qt.rd("l", cfg_addr(p + 8))
            caps[ctype] = (bar, off, p)
        p = nxt
    assert all(caps[t][0] == 4 for t in (1, 2, 4)), caps
    common = BAR4 + caps[1][1]
    notify = BAR4 + caps[2][1]
    mult = qt.rd("l", cfg_addr(caps[2][2] + 16))
    devcfg = BAR4 + caps[4][1]

    qt.wr("b", common + 0x14, 0)
    qt.wr("b", common + 0x14, 1 | 2)
    qt.wr("l", common + 0x00, 1)
    hi = qt.rd("l", common + 0x04)
    assert hi & 1, "no VERSION_1"
    qt.wr("l", common + 0x08, 0); qt.wr("l", common + 0x0c, 0)
    qt.wr("l", common + 0x08, 1); qt.wr("l", common + 0x0c, 1)
    qt.wr("b", common + 0x14, 1 | 2 | 8)
    assert qt.rd("b", common + 0x14) & 8, "FEATURES_OK refused"
    nq = qt.rd("w", common + 0x12)
    assert nq == 3, nq
    rings = []
    base = RAM + 0x100000
    for q in range(3):
        qt.wr("w", common + 0x16, q)
        size = qt.rd("w", common + 0x18)
        assert size == QSZ, size
        noff = qt.rd("w", common + 0x1e)
        r = Ring(qt, q, base, notify + noff * mult)
        for o, v in ((0x20, r.desc), (0x28, r.avail), (0x30, r.used)):
            qt.wr("q", common + o, v)
        qt.wr("w", common + 0x1c, 1)
        rings.append(r)
        base += 0x800000
    qt.wr("b", common + 0x14, 1 | 2 | 4 | 8)
    cid = qt.rd("q", devcfg)
    print("guest_cid from config:", cid)
    assert cid == GUEST_CID
    rx, tx, ev = rings

    rx_buf = {}
    for i in range(64):
        a = rx.bufs + i * RXBUF
        d = rx.add(a, RXBUF, True)
        rx_buf[d] = a
    rx.kick()

    tx_slot = [0]

    def send(hdr_fields, data=b""):
        src_port, dst_port, op, flags, fwd = hdr_fields
        pkt = HDR.pack(GUEST_CID, 2, src_port, dst_port, len(data), 1, op, flags,
                       GUEST_BUF_ALLOC, fwd) + data
        for d, _ in tx.reap():
            tx.free.append(d)
        a = tx.bufs + (tx_slot[0] % 64) * 0x11000
        tx_slot[0] += 1
        tx.add(a, len(pkt), False, pkt)
        tx.kick()

    # guest vsock state per (host_port, guest_port)
    conns = {}
    stats = {"credit_req": 0, "credit_upd_from_host": 0, "rw_in": 0}

    def poll_guest():
        progressed = False
        for d, ln in rx.reap():
            progressed = True
            a = rx_buf[d]
            raw = qt.mread(a, ln)
            (src_cid, dst_cid, sport, dport, plen, typ, op, flags, balloc, fwd) = HDR.unpack(raw[:44])
            data = raw[44:44 + plen]
            rx.free.append(d)
            d2 = rx.add(a, RXBUF, True)
            rx_buf[d2] = a
            key = (sport, dport)
            c = conns.get(key)
            if op == OP_REQUEST:
                if dport == 1339:
                    conns[key] = {"fwd": 0, "tx": 0, "pb": balloc, "pf": fwd, "pending": b"", "shut": 0}
                    send((dport, sport, OP_RESPONSE, 0, 0))
                else:
                    send((dport, sport, OP_RST, 0, 0))
                print("guest: REQUEST %d->%d buf_alloc=%d" % (sport, dport, balloc))
                continue
            if not c:
                if op != OP_RST:
                    print("guest: %s for unknown conn" % OPN.get(op))
                continue
            c["pb"], c["pf"] = balloc, fwd
            if op == OP_RW:
                stats["rw_in"] += 1
                c["fwd"] += len(data)  # consumed at once; credit update only when asked
                c["pending"] += data
            elif op == OP_CREDIT_REQUEST:
                stats["credit_req"] += 1
                send((dport, sport, OP_CREDIT_UPDATE, 0, c["fwd"]))
            elif op == OP_CREDIT_UPDATE:
                stats["credit_upd_from_host"] += 1
            elif op == OP_SHUTDOWN:
                c["shut"] |= flags
                print("guest: SHUTDOWN flags=%d" % flags)
                # host done sending: echo everything, then close (SHUTDOWN both)
            elif op == OP_RST:
                print("guest: RST")
                del conns[key]
                continue
        rx.kick()
        # echo pending data back within the host's credit
        for key, c in list(conns.items()):
            hport, gport = key
            while c["pending"]:
                credit = c["pb"] - (c["tx"] - c["pf"])
                if credit <= 0:
                    break
                chunk = c["pending"][:min(credit, 0x10000)]
                c["pending"] = c["pending"][len(chunk):]
                send((gport, hport, OP_RW, 0, c["fwd"]), chunk)
                c["tx"] += len(chunk)
                progressed = True
            if c["shut"] & 2 and not c["pending"] and not c.get("closed"):
                c["closed"] = True
                send((gport, hport, OP_SHUTDOWN, 3, c["fwd"]))
                progressed = True
        return progressed

    stop = threading.Event()

    def guest_loop():
        while not stop.is_set():
            if not poll_guest():
                time.sleep(0.002)

    # refused port: guest RSTs, host connection closes
    gt = threading.Thread(target=guest_loop, daemon=True)
    gt.start()
    s = host_connect(4242)
    try:
        r = s.recv(100)
    except ConnectionResetError:
        r = b""
    print("refused port: CONNECT 4242 ->", r or b"<closed>")
    assert r == b""
    s.close()

    # echo test
    total = int(os.environ.get("ECHO_BYTES", str(1024 * 1024)))
    payload = os.urandom(total)
    s = host_connect(1339, timeout=60)
    f = s.makefile("rb")
    ok = f.readline()
    print("CONNECT 1339 ->", ok)
    assert ok.startswith(b"OK ")
    t0 = time.time()

    def writer():
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
    wt = threading.Thread(target=writer, daemon=True)
    wt.start()
    got = bytearray()
    while True:
        chunk = f.read1(65536) if hasattr(f, "read1") else s.recv(65536)
        if not chunk:
            break
        got += chunk
    dt = time.time() - t0
    print("echoed %d of %d bytes in %.1f s, match=%s" % (len(got), total, dt, bytes(got) == payload))
    print("stats:", stats)
    stop.set()
    gt.join()
    assert bytes(got) == payload
    assert stats["credit_req"] > 0, "device never asked for credit"
    print("PASS")


if __name__ == "__main__":
    main()
