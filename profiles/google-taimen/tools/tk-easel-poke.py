#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok
"""Poke Easel registers live through BAR2 (HANDOFF §42b).

  tk-easel-poke.py 0x040110e8 1      write
  tk-easel-poke.py 0x04011110        read

Requires easel-mipi loaded with the BAR2 window at 0x04000000.
"""
import mmap
import os
import struct
import sys

DEV = "/sys/bus/pci/devices/0000:01:00.0/resource2"
PERIPH = 0x04000000


def main():
    addr = int(sys.argv[1], 0)
    fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, os.path.getsize(DEV), mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE)
    off = addr - PERIPH

    if len(sys.argv) > 2:
        val = int(sys.argv[2], 0)
        struct.pack_into("<I", m, off, val)
        print("0x%08x <- 0x%08x, reads 0x%08x" %
              (addr, val, struct.unpack_from("<I", m, off)[0]))
    else:
        print("0x%08x = 0x%08x" % (addr, struct.unpack_from("<I", m, off)[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
