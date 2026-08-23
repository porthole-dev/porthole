#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok
"""Sweep the CSIPHY settle count over its whole 0..255 range against a LIVE
transmitting sensor.

The earlier sweep only covered 12..20, because that is the neighbourhood
csiphy_settle_cnt_calc() lands in for a 170 MHz link on a 200 MHz timer. That is
only the right neighbourhood if every input to that calculation is right. If the
timer clock, the link rate or the formula itself is off, the correct value can be
anywhere -- and a D-PHY that never achieves sync can report nothing at all rather
than reporting errors, which is indistinguishable from "no signal".

Preconditions (both hard-won, do not run without them):
  * the sensor is confirmed transmitting -- imx179 register 0x0012 advances
  * camss holds the pipeline open, so the PHY is configured and clocked

Writes settle to every lane block's CFG2 (0x008) including the clock lane at
0x700, clears the latched status, waits, and reads all eleven status words.
Any non-zero word is signal.

Run ON the phone, as root.
"""
import mmap, os, struct, sys, time

PHY = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x0CA35000
DWELL = float(sys.argv[2]) if len(sys.argv) > 2 else 0.10
PAGE = 0x1000
LANES = (0x000, 0x200, 0x400, 0x600, 0x700)


def main():
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, PAGE, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=PHY)
    r = lambda o: struct.unpack_from("<I", m, o)[0]
    w = lambda o, v: struct.pack_into("<I", m, o, v)

    print("ctrl5=%02x ctrl6=%02x, sweeping settle 0..255 at %.2fs" %
          (r(0x814), r(0x818), DWELL))
    hits = 0
    try:
        for settle in range(256):
            for base in LANES:
                w(base + 0x008, settle)
            for i in range(11):                       # clear latched status
                w(0x800 + 4 * (22 + i), 0xFF)
            time.sleep(DWELL)
            st = [r(0x8B0 + 4 * i) for i in range(11)]
            if any(st):
                hits += 1
                print("  *** HIT settle=%d (0x%02x) -> %s" %
                      (settle, settle, " ".join("%02x" % s for s in st)))
    finally:
        m.close()
        os.close(fd)
    print("\n%d hit(s) across the full settle range" % hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
