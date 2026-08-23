#!/usr/bin/env python3
# scope: generic
"""Sweep CSIPHY CMN_CTRL5 (the lane-enable mask) against a LIVE transmitting
sensor and watch the eleven status registers.

Preconditions, both of which took a long time to establish -- do not run this
without them:
  * the sensor is confirmed transmitting (imx179 register 0x0012 advances), and
  * camss holds the pipeline open, so the PHY is configured and clocked.

Then the only variable left is which lanes the PHY listens to. The driver
computes 0xd5 from the DT (four data lanes at positions 0-3, plus the clock
lane), and the vendor's own v5.0.1 table also says cmn_ctrl5 = 0xD5 -- but if
the board routes the lanes differently, or the clock lane sits somewhere other
than block 0x700, 0xd5 is listening to the wrong pins and the PHY is silent
exactly the way this one is.

mmap rather than one peek.py per register: a 256-value sweep needs ~3000 reads
and process startup would dominate.

Run ON the phone, as root.
"""
import mmap, os, struct, sys, time

MMIO = 0x0CA35000            # CSIPHY1
PAGE = 0x1000
CTRL5 = 0x814
STATUS0 = 0x8B0
NSTATUS = 11


def main():
    dwell = float(sys.argv[1]) if len(sys.argv) > 1 else 0.15
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, PAGE, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=MMIO)

    def r32(off):
        return struct.unpack_from("<I", m, off)[0]

    def w32(off, val):
        struct.pack_into("<I", m, off, val)

    orig = r32(CTRL5)
    print("CSIPHY1 cmn_ctrl5 currently 0x%02x, cmn_ctrl6 0x%02x" % (orig, r32(0x818)))
    print("sweeping cmn_ctrl5 0x00..0xff, dwell %.2fs -- any non-zero status is a HIT\n" % dwell)

    hits = 0
    try:
        for val in range(0x100):
            w32(CTRL5, val)
            # clear whatever is latched, then let the PHY look at the wires
            for i in range(NSTATUS):
                w32(0x800 + 4 * (22 + i), 0xFF)
            time.sleep(dwell)
            st = [r32(STATUS0 + 4 * i) for i in range(NSTATUS)]
            if any(st):
                hits += 1
                print("  *** HIT ctrl5=0x%02x -> %s" %
                      (val, " ".join("%02x" % s for s in st)))
    finally:
        w32(CTRL5, orig)
        m.close()
        os.close(fd)

    print("\n%d hit(s). ctrl5 restored to 0x%02x" % (hits, orig))
    return 0


if __name__ == "__main__":
    sys.exit(main())
