#!/usr/bin/env python3
# scope: soc:msm8998
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Hand-configure ALL THREE msm8998 CSIPHYs and watch every status register.

The pipeline must already be streaming on one PHY so CAMSS_TOP and the AHB
are up; this then programs the other two by hand with the same v5.0.1 2-phase
sequence and polls all three. If Easel's TX lands on a PHY other than the one
the devicetree names, this finds it without a reflash.
"""
import mmap, os, struct, sys, time

BASES = {0: 0x0ca34000, 1: 0x0ca35000, 2: 0x0ca36000}
SETTLE = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x0e

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = {i: mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=b)
     for i, b in BASES.items()}

def w(i, o, v): struct.pack_into("<I", m[i], o, v)
def r(i, o):    return struct.unpack_from("<I", m[i], o)[0]

LANE = [(0x004, 0x0c), (0x02c, 0x01), (0x034, 0x0f), (0x01c, 0x0a),
        (0x014, 0x60), (0x03c, 0xb8), (0x000, 0x91), (0x010, 0x52),
        (0x038, 0xfe), (0x024, 0x04)]
CLK  = [(0x704, 0x0c), (0x72c, 0x01), (0x734, 0x0f), (0x71c, 0x0a),
        (0x714, 0x60), (0x728, 0x04), (0x73c, 0xb8), (0x700, 0x80),
        (0x710, 0x52), (0x738, 0xfe), (0x70c, 0xa5), (0x724, 0x04)]
MASKS = [0xff, 0xff, 0xfb, 0xff, 0x7f, 0xff, 0xff, 0xef, 0xff, 0xff, 0xff]

def configure(i):
    w(i, 0x800, 0x1); time.sleep(0.006); w(i, 0x800, 0x0)
    for ln in (0x000, 0x200, 0x400, 0x600):
        w(i, ln + 0x008, SETTLE)
    w(i, 0x708, SETTLE)
    w(i, 0x814, 0xd5)
    w(i, 0x818, 0x01)
    w(i, 0x81c, 0x02)
    for ln in (0x000, 0x200, 0x400, 0x600):
        for o, v in LANE:
            w(i, ln + o, v)
    for o, v in CLK:
        w(i, o, v)
    for k, v in enumerate(MASKS):
        w(i, 0x82c + 4 * k, v)

def status(i):
    return " ".join("%02x" % r(i, 0x8b0 + 4 * k) for k in range(11))

print("hw versions:", {i: hex(r(i, 0x8ec)) for i in BASES})
for i in BASES:
    try:
        configure(i)
        print("configured phy%d, ctrl5=0x%02x ctrl6=0x%02x lane0=0x%02x" %
              (i, r(i, 0x814), r(i, 0x818), r(i, 0x000)))
    except Exception as e:
        print("phy%d configure failed: %s" % (i, e))

for n in range(6):
    time.sleep(1.0)
    for i in BASES:
        s = status(i)
        if s.replace("00", "").strip():
            print("*** phy%d ACTIVITY: %s" % (i, s))
    print("t=%ds  " % (n + 1) + " | ".join("phy%d %s" % (i, status(i)) for i in BASES))
