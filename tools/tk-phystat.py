#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Read (and optionally poke) the SoC CSIPHY common block live.

  tk-phystat.py [phybase] [ctrl6val]
"""
import mmap, os, struct, sys

base = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x0ca35000
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
              mmap.PROT_READ | mmap.PROT_WRITE, offset=base)

if len(sys.argv) > 2:
    struct.pack_into("<I", m, 0x818, int(sys.argv[2], 0))

st = [struct.unpack_from("<I", m, 0x8b0 + 4 * i)[0] for i in range(20)]
ctl = [struct.unpack_from("<I", m, 0x800 + 4 * i)[0] for i in range(8)]
print("ctrl0-7: %s" % " ".join("%02x" % v for v in ctl))
print("status0-19: %s" % " ".join("%02x" % v for v in st))
