#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok
"""Dump an MMIO range via /dev/mem, one mmap'd process.

peek.py is fine for a handful of registers but costs a process start each; this
is for sweeping whole blocks, which is what you want once you have run out of
registers you can name a hypothesis for.

  ph-regdump.py 0x0ca10000 0x000 0x100      # VFE0 core
  ph-regdump.py 0x0ca10000 0x400 0x0a0      # VFE0 bus bridge

Run ON the phone, as root, while the block is clocked -- reading an unclocked
Qualcomm block hangs the bus and takes the watchdog with it.
"""
import mmap, os, struct, sys

def main():
    base = int(sys.argv[1], 0)
    start = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
    length = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x100

    # mmap's offset must be page-aligned, and a base that is not (0x17914800,
    # an ACD block) fails as a bare "OSError: [Errno 22] Invalid argument"
    # that reads like a dead peripheral rather than a bad argument. Fold the
    # misalignment into the offsets so any base works and the printed +off
    # still counts from the base the caller asked about.
    skew = base & 0xFFF
    base -= skew
    start += skew

    page = (start + length + 0xFFF) & ~0xFFF
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    m = mmap.mmap(fd, page, mmap.MAP_SHARED, mmap.PROT_READ, offset=base)

    for off in range(start, start + length, 16):
        vals = []
        for k in range(0, 16, 4):
            vals.append("%08x" % struct.unpack_from("<I", m, off + k)[0])
        if any(v != "00000000" for v in vals):
            print("  +%04x: %s" % (off - skew, " ".join(vals)))
    m.close()
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
