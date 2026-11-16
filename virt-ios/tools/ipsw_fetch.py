#!/usr/bin/env python3
"""List or extract members of a remote IPSW (zip/zip64) with HTTP range requests.

  ipsw_fetch.py URL                       list members (name, size)
  ipsw_fetch.py URL OUTDIR NAME [NAME..]  extract members whose name equals NAME

Members stream to OUTDIR/<basename>.part in chunks, resume if interrupted,
CRC-32 is checked, then the file is renamed to its final name.
"""
import os, struct, subprocess, sys, zlib

CHUNK = 16 << 20


def curl(*args):
    return subprocess.run(['curl', '-sfL', '--retry', '5', '--max-time', '600', *args],
                          check=True, capture_output=True).stdout


def get(url, start, end):
    data = curl('-r', f'{start}-{end}', url)
    if len(data) != end - start + 1:
        raise IOError(f'bad range reply: {len(data)} bytes for {start}-{end}')
    return data


def total_size(url):
    for line in curl('-I', url).decode(errors='replace').splitlines():
        if line.lower().startswith('content-length:'):
            return int(line.split(':', 1)[1])
    raise IOError('no content-length')


def central_directory(url):
    size = total_size(url)
    tail = get(url, max(0, size - 65558), size - 1)
    eocd = tail.rfind(b'PK\x05\x06')
    if eocd < 0:
        raise ValueError('no end of central directory')
    _, _, _, _, count, cd_size, cd_off, _ = struct.unpack_from('<4s4H2LH', tail, eocd)
    loc = tail.rfind(b'PK\x06\x07', 0, eocd)
    if loc >= 0:  # zip64
        z64_off = struct.unpack_from('<Q', tail, loc + 8)[0]
        z64 = get(url, z64_off, z64_off + 55)
        count, cd_size, cd_off = struct.unpack_from('<QQQ', z64, 32)
    cd = get(url, cd_off, cd_off + cd_size - 1)
    out, p = [], 0
    for _ in range(count):
        (sig, _, _, _, method, _, _, crc, csize, usize, nlen, xlen, clen,
         _, _, _, lho) = struct.unpack_from('<4s6H3L5H2L', cd, p)
        assert sig == b'PK\x01\x02'
        name = cd[p + 46:p + 46 + nlen].decode('utf-8', 'replace')
        extra = cd[p + 46 + nlen:p + 46 + nlen + xlen]
        q = 0
        while q + 4 <= len(extra):
            tag, ln = struct.unpack_from('<HH', extra, q)
            if tag == 1:  # zip64 extended info, fields present only when saturated
                vals, r = [], q + 4
                for present in (usize == 0xffffffff, csize == 0xffffffff, lho == 0xffffffff):
                    if present:
                        vals.append(struct.unpack_from('<Q', extra, r)[0]); r += 8
                    else:
                        vals.append(None)
                usize = vals[0] if vals[0] is not None else usize
                csize = vals[1] if vals[1] is not None else csize
                lho = vals[2] if vals[2] is not None else lho
            q += 4 + ln
        out.append(dict(name=name, method=method, crc=crc, csize=csize, usize=usize, lho=lho))
        p += 46 + nlen + xlen + clen
    return out


def extract(url, m, outdir):
    final = os.path.join(outdir, os.path.basename(m['name']))
    if os.path.exists(final):
        print(f'exists: {final}'); return
    hdr = get(url, m['lho'], m['lho'] + 29)
    nlen, xlen = struct.unpack_from('<HH', hdr, 26)
    start = m['lho'] + 30 + nlen + xlen
    end = start + m['csize']
    part = final + '.part'
    # Resume only for stored members; deflated ones restart (decompressor state).
    done = os.path.getsize(part) if (m['method'] == 0 and os.path.exists(part)) else 0
    crc = 0
    if done:
        with open(part, 'rb') as f:
            for blk in iter(lambda: f.read(CHUNK), b''):
                crc = zlib.crc32(blk, crc)
    dec = zlib.decompressobj(-15) if m['method'] == 8 else None
    if m['method'] not in (0, 8):
        raise ValueError(f'unsupported method {m["method"]}')
    with open(part, 'ab' if done else 'wb') as f:
        pos = start + done
        while pos < end:
            blk = get(url, pos, min(pos + CHUNK, end) - 1)
            pos += len(blk)
            data = dec.decompress(blk) if dec else blk
            crc = zlib.crc32(data, crc)
            f.write(data)
            print(f'\r{os.path.basename(final)}: {100 * (pos - start) // m["csize"]}%', end='', flush=True)
        if dec:
            data = dec.flush(); crc = zlib.crc32(data, crc); f.write(data)
    print()
    if crc != m['crc']:
        raise IOError(f'CRC mismatch for {m["name"]}: {crc:#x} != {m["crc"]:#x}')
    os.replace(part, final)
    print(f'ok: {final} ({os.path.getsize(final)} bytes)')


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 2
    url = sys.argv[1]
    members = central_directory(url)
    if len(sys.argv) == 2:
        for m in members:
            print(f'{m["usize"]:>12}  {m["name"]}')
        return 0
    outdir, names = sys.argv[2], sys.argv[3:]
    os.makedirs(outdir, exist_ok=True)
    for n in names:
        hits = [m for m in members if m['name'] == n]
        if not hits:
            print(f'not found: {n}', file=sys.stderr); return 1
        extract(url, hits[0], outdir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
