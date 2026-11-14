#!/usr/bin/env python3
"""Talk to vphoned (vphone-cli's guest daemon, HTTP on vsock port 1339) from Windows.

The Windows counterpart of vphone-linux's tools/vphoned.sh. run-vphone.ps1 gives the VM a
virtio-vsock-sock-pci whose host side listens on tcp 127.0.0.1:<VsockPort> (default 13390)
and speaks Firecracker's hybrid-vsock protocol: send "CONNECT <port>\\n", get "OK <n>\\n",
then the stream is the guest's port. Pure Python 3, no dependencies.

  python vphoned.py [--port N] METHOD PATH [BODY]
  python vphoned.py GET /v1/health
  python vphoned.py GET /v1/device
  python vphoned.py POST /v1/rpc '{"method":"screen.unlock","params":{},"id":1}'
  python vphoned.py POST /v1/rpc @req.json      # body from a file
  echo {...} | python vphoned.py POST /v1/rpc -  # body from stdin

Windows PowerShell 5.1 drops the double quotes inside an argument passed to a native program;
there write the JSON with escaped quotes ('{\\"method\\":...}'), or use @file or - (stdin).

The port defaults to $VPHONE_VSOCK_PORT or 13390. Like vphoned.sh, the write side stays open
until the answer is complete: vphoned drops a half-closed connection before it answers an RPC.
Prints the response body (-i: with the status line and headers). Exit status: 0 for an HTTP
2xx/3xx answer, 2 for 4xx/5xx, 1 when vphoned could not be reached.
"""
import argparse
import os
import socket
import sys


def read_line(sock, buf):
    """One \\n-terminated line from sock; buf keeps the bytes after it."""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    line, _, rest = bytes(buf).partition(b"\n")
    buf[:] = rest
    return line.rstrip(b"\r")


def read_exact(sock, buf, n):
    while len(buf) < n:
        chunk = sock.recv(max(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("connection closed after %d of %d bytes" % (len(buf), n))
        buf += chunk
    data = bytes(buf[:n])
    del buf[:n]
    return data


def read_response(sock, buf, method):
    status_line = read_line(sock, buf)
    parts = status_line.split(b" ", 2)
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/"):
        raise ConnectionError("not an HTTP answer: %r" % status_line[:80])
    status = int(parts[1])
    headers = []
    while True:
        line = read_line(sock, buf)
        if not line:
            break
        name, _, value = line.partition(b":")
        headers.append((name.strip().lower(), value.strip()))
    hdr = dict(headers)

    if method == "HEAD" or status in (204, 304) or 100 <= status < 200:
        body = b""
    elif b"chunked" in hdr.get(b"transfer-encoding", b"").lower():
        body = bytearray()
        while True:
            size = int(read_line(sock, buf).split(b";")[0], 16)
            if size == 0:
                while read_line(sock, buf):  # trailers
                    pass
                break
            body += read_exact(sock, buf, size)
            read_line(sock, buf)
        body = bytes(body)
    elif b"content-length" in hdr:
        body = read_exact(sock, buf, int(hdr[b"content-length"]))
    else:
        while True:  # until the guest closes
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        body = bytes(buf)
        buf.clear()
    return status_line, headers, body


def main():
    ap = argparse.ArgumentParser(
        description="Talk to vphoned over the VM's vsock (virtio-vsock-sock-pci).")
    ap.add_argument("--host", default=os.environ.get("VPHONE_VSOCK_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("VPHONE_VSOCK_PORT", "13390")),
                    help="the VM's vsock TCP port (run-vphone.ps1 -VsockPort; default 13390)")
    ap.add_argument("--vport", type=int, default=1339, help="guest vsock port (vphoned: 1339)")
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("VPHONED_TIMEOUT", "300")))
    ap.add_argument("-i", "--include", action="store_true", help="print status line and headers")
    ap.add_argument("-o", "--output", help="write the body to this file instead of stdout")
    ap.add_argument("method")
    ap.add_argument("path")
    ap.add_argument("body", nargs="?", default="",
                    help="request body (JSON), @FILE for a file's contents, - for stdin")
    a = ap.parse_args()

    method = a.method.upper()
    if a.body == "-":
        body = sys.stdin.buffer.read()
    elif a.body.startswith("@"):
        with open(a.body[1:], "rb") as f:
            body = f.read()
    else:
        body = a.body.encode("utf-8")

    req = ["%s %s HTTP/1.1" % (method, a.path), "Host: localhost", "Connection: close"]
    if body or method in ("POST", "PUT", "PATCH"):
        req.append("Content-Type: application/json")
        req.append("Content-Length: %d" % len(body))
    req = ("\r\n".join(req) + "\r\n\r\n").encode("ascii") + body

    try:
        sock = socket.create_connection((a.host, a.port), timeout=a.timeout)
    except OSError as e:
        sys.exit("vphoned.py: cannot connect to %s:%d (is the VM running with -VsockPort %d?): %s"
                 % (a.host, a.port, a.port, e))
    with sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        buf = bytearray()
        try:
            sock.sendall(b"CONNECT %d\n" % a.vport)
            ok = read_line(sock, buf)
        except (OSError, ConnectionError) as e:
            sys.exit("vphoned.py: no answer to CONNECT %d (guest not up, or vphoned not "
                     "listening): %s" % (a.vport, e))
        if not ok.startswith(b"OK"):
            sys.exit("vphoned.py: CONNECT %d refused: %r" % (a.vport, ok))
        try:
            sock.sendall(req)  # the write side stays open until the answer is in
            status_line, headers, resp = read_response(sock, buf, method)
        except socket.timeout:
            sys.exit("vphoned.py: timed out after %g s" % a.timeout)
        except (OSError, ConnectionError, ValueError) as e:
            sys.exit("vphoned.py: %s" % e)

    status = int(status_line.split(b" ")[1])
    out = sys.stdout.buffer
    if a.include:
        out.write(status_line + b"\r\n")
        for name, value in headers:
            out.write(name + b": " + value + b"\r\n")
        out.write(b"\r\n")
    if a.output:
        with open(a.output, "wb") as f:
            f.write(resp)
    else:
        out.write(resp)
        if not resp.endswith(b"\n"):
            out.write(b"\n")
    out.flush()
    return 2 if status >= 400 else 0


if __name__ == "__main__":
    sys.exit(main())
