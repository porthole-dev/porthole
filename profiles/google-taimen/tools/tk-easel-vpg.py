#!/usr/bin/env python3
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Drive Easel's TX video pattern generator -- sensor and bypass mux out of the
picture entirely.

If the msm8998 CSID's per-lane MISR still reads zero while Easel's TX VPG is
emitting RAW10 frames, the fault is 100% on the SoC side and Easel is
exonerated. That is the cheapest decisive experiment available (HANDOFF §15).

  tk-easel-vpg.py <txdev> on|off
"""
import mmap, os, struct, sys

DEV = "/sys/bus/pci/devices/0000:01:00.0/resource2"
PERIPH = 0x04000000
TX = {0: 0x04010000, 1: 0x04011000}
TOP = 0x04015000

VPG_CTRL, VPG_STATUS, VPG_MODE_CFG = 0x80, 0x84, 0x88
VPG_PKT_CFG, VPG_PKT_SIZE = 0x8c, 0x90
VPG_HLINE_TIME, VPG_VSA, VPG_VBP, VPG_VFP, VPG_ACT = 0x9c, 0xa0, 0xa4, 0xa8, 0xac

dev = int(sys.argv[1]) if len(sys.argv) > 1 else 1
on = (len(sys.argv) < 3) or sys.argv[2] != "off"
base = TX[dev]

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, os.path.getsize(DEV), mmap.MAP_SHARED,
              mmap.PROT_READ | mmap.PROT_WRITE)
r = lambda a: struct.unpack_from("<I", m, a - PERIPH)[0]
w = lambda a, v: struct.pack_into("<I", m, a - PERIPH, v)

if not on:
    w(base + VPG_CTRL, 0)
    print("VPG off, CTRL=0x%08x STATUS=0x%08x" %
          (r(base + VPG_CTRL), r(base + VPG_STATUS)))
    raise SystemExit(0)

# 1640x922 RAW10 (DT 0x2b), VC0, colour-bar mode
w(base + VPG_MODE_CFG, 0)           # VPG_MODE 0 = vertical colour bar
w(base + VPG_PKT_CFG, 0x2b)         # DT = RAW10, VC 0
w(base + VPG_PKT_SIZE, 1640)
w(base + VPG_HLINE_TIME, 3444)
w(base + VPG_VSA, 4)
w(base + VPG_VBP, 8)
w(base + VPG_VFP, 8)
w(base + VPG_ACT, 922)
w(base + VPG_CTRL, 1)               # VPG_EN

print("TX%d VPG on: CTRL=0x%08x STATUS=0x%08x PKT_CFG=0x%08x ACT=%d" %
      (dev, r(base + VPG_CTRL), r(base + VPG_STATUS),
       r(base + VPG_PKT_CFG), r(base + VPG_ACT)))
print("  TX PHY_STATUS=0x%08x  TOP TX%d_MODE=0x%08x" %
      (r(base + 0x110), dev, r(TOP + (0x28 if dev else 0x0))))
