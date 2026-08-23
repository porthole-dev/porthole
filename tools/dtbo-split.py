#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Split an Android DTBO image into its individual .dtb entries.

Header (all big-endian):
  magic u32 = 0xd7b7ab1e, total_size, header_size, dt_entry_size,
  dt_entry_count, dt_entries_offset, page_size, version
Each entry:
  dt_size, dt_offset, id, rev, custom[4]
"""
import struct
import sys
import os

path = sys.argv[1]
outdir = sys.argv[2]
os.makedirs(outdir, exist_ok=True)

blob = open(path, "rb").read()
magic, total, hdrsz, entsz, entcnt, entoff, pagesz, ver = struct.unpack(">8I", blob[:32])
assert magic == 0xD7B7AB1E, f"not a dtbo image: {magic:#x}"
print(f"dtbo: {entcnt} entries, page {pagesz}, version {ver}")

for i in range(entcnt):
    off = entoff + i * entsz
    dt_size, dt_off, did, drev = struct.unpack(">4I", blob[off:off + 16])
    out = os.path.join(outdir, f"entry{i:02d}_id{did}.dtb")
    with open(out, "wb") as f:
        f.write(blob[dt_off:dt_off + dt_size])
    print(f"  entry {i:2d}  id={did:5d} rev={drev}  size={dt_size:7d}  -> {os.path.basename(out)}")
