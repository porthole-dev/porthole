#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Minimal flattened-devicetree dumper (no dtc on this host).

Usage: fdtdump.py FILE.dtb [substring ...]
With substrings, prints only nodes whose path or properties match any of them.
"""
import struct
import sys

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9

blob = open(sys.argv[1], "rb").read()
filters = [s.lower() for s in sys.argv[2:]]

magic, total, off_struct, off_strings, off_rsv, ver, lastcomp, boot_cpu, sz_strings, sz_struct = \
    struct.unpack(">10I", blob[:40])
assert magic == 0xD00DFEED, f"not a dtb: {magic:#x}"


def cstr(buf, off):
    end = buf.index(b"\0", off)
    return buf[off:end].decode("utf-8", "replace")


def fmt(name, val):
    """Best-effort property rendering."""
    if not val:
        return ""
    # printable string / stringlist?
    if val[-1:] == b"\0" and all(32 <= c < 127 or c == 0 for c in val[:-1]):
        parts = [p.decode() for p in val[:-1].split(b"\0")]
        return " = " + ", ".join(f'"{p}"' for p in parts)
    if len(val) % 4 == 0:
        cells = struct.unpack(f">{len(val)//4}I", val)
        return " = <" + " ".join(f"0x{c:x}" for c in cells) + ">"
    return " = [" + " ".join(f"{b:02x}" for b in val) + "]"


pos = off_struct
depth = 0
path = []
nodes = []           # (path, [(prop, rendered)])
cur = None

while pos < off_struct + sz_struct:
    (tok,) = struct.unpack(">I", blob[pos:pos + 4])
    pos += 4
    if tok == FDT_BEGIN_NODE:
        name = cstr(blob, pos)
        pos += (len(name.encode()) + 4) & ~3
        path.append(name)
        cur = ("/".join(path), [])
        nodes.append(cur)
        depth += 1
    elif tok == FDT_END_NODE:
        path.pop()
        depth -= 1
        cur = None
    elif tok == FDT_PROP:
        plen, poff = struct.unpack(">II", blob[pos:pos + 8])
        pos += 8
        val = blob[pos:pos + plen]
        pos += (plen + 3) & ~3
        pname = cstr(blob, off_strings + poff)
        if cur:
            cur[1].append((pname, fmt(pname, val)))
    elif tok in (FDT_NOP,):
        continue
    elif tok == FDT_END:
        break
    else:
        raise SystemExit(f"bad token {tok} at {pos:#x}")

for p, props in nodes:
    blobtext = (p + " " + " ".join(n + v for n, v in props)).lower()
    if filters and not any(f in blobtext for f in filters):
        continue
    print(f"\n=== {p or '/'} ===")
    for n, v in props:
        print(f"    {n}{v}")
