#!/usr/bin/env python3
# scope: soc:msm8998
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Sample TLMM GPIO input levels on the phone -- runs ON THE DEVICE.

Is the MI2S bit clock actually coming out of the pad?  q6afe reporting ret=0
means the ADSP accepted a request, not that a clock exists (HANDOFF-audio.md
9.6); the pad input register is the only thing on the APPS side that can
answer.  A 48 kHz word clock cannot read all-low over hundreds of samples.

Section 5 rule 0: TLMM is APPS-owned and pinctrl-msm reads exactly these
registers, so this is inside the rules.  Do not point it at another block.

    tk-pins.py 58 59 61 [-n 400]
"""
import mmap
import os
import struct
import sys

TLMM = 0x03400000
STRIDE = 0x1000
CFG = 0x0
IN_OUT = 0x4

# msm8998's TLMM is TILED: the per-pin registers are NOT at base + pin*0x1000.
# Each pin lives in one of three tiles and PINGROUP(id, tile, ...) in
# pinctrl-msm8998.c puts it at base + tile + id*0x1000.  Ignoring this reads a
# hole in the 12 MB window that returns 0 for every pin, which is
# indistinguishable from "the pin is idle" -- and that is exactly the artefact
# that produced the dead root cause in HANDOFF-audio.md 9.7.  Ranges are
# generated from the PINGROUP table.
NORTH, WEST, EAST = 0x500000, 0x100000, 0x900000
TILES = [(0, 3, EAST), (4, 7, WEST), (8, 34, EAST), (35, 37, NORTH),
         (38, 39, WEST), (40, 48, EAST), (49, 52, NORTH), (53, 84, WEST),
         (85, 96, EAST), (97, 104, WEST), (105, 113, NORTH), (114, 116, WEST),
         (117, 126, EAST), (127, 129, WEST), (130, 131, NORTH), (132, 149, WEST)]


def pin_base(p):
    for lo, hi, tile in TILES:
        if lo <= p <= hi:
            return TLMM + tile + p * STRIDE
    raise SystemExit(f"pin {p} is outside the msm8998 TLMM map")


def _open(fd, addr):
    page = mmap.PAGESIZE
    base = addr & ~(page - 1)
    return mmap.mmap(fd, page, mmap.MAP_SHARED, mmap.PROT_READ, offset=base), addr - base


def rd(m, off):
    # 32-bit accesses only: these blocks do not promise byte-wide reads, and a
    # byte read that silently returns 0 looks exactly like a static pin.
    return struct.unpack("<I", m[off:off + 4])[0]


def sample(pins, n):
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    maps, cfgs = {}, {}
    try:
        for p in pins:
            maps[p] = _open(fd, pin_base(p) + IN_OUT)
            cm, coff = _open(fd, pin_base(p) + CFG)
            cfgs[p] = rd(cm, coff)
            cm.close()
        counts = {p: [0, 0] for p in pins}
        for _ in range(n):
            for p in pins:
                m, off = maps[p]
                counts[p][rd(m, off) & 1] += 1
        return counts, cfgs
    finally:
        for m, _ in maps.values():
            m.close()
        os.close(fd)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    n = 400
    if "-n" in sys.argv:
        n = int(sys.argv[sys.argv.index("-n") + 1])
        args = [a for a in args if a != str(n)]
    pins = [int(a) for a in args] or [58, 59, 61]
    counts, cfgs = sample(pins, n)
    for p, (lo, hi) in counts.items():
        verdict = "STATIC" if lo == 0 or hi == 0 else "TOGGLING"
        c = cfgs[p]
        # CFG: [1:0] pull, [4:2] func sel, [10:6] drive strength, [9] oe
        print(f"  pin {p:3d}: low={lo:<5d} high={hi:<5d} -> {verdict:8s} "
              f"cfg=0x{c:08x} func={(c >> 2) & 0x7} oe={(c >> 9) & 1} "
              f"drv={2 * (((c >> 6) & 0x7) + 1)}mA pull={c & 3}")


if __name__ == "__main__":
    main()
