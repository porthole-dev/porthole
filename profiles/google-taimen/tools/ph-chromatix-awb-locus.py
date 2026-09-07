#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Extract the AWB colour-temperature locus from a vendor chromatix 3a blob.

The chromatix 3a library is a stub ELF whose entire payload is one .data blob
returned by load_chromatix(). Its first field after the 48-byte header is the
AWB reference-point table: 16 (R/G, B/G) raw chromaticity pairs at .data+0x30,
one per illuminant slot, with several slots aliased to the same measurement.

We key each point by what libipa's estimateCCT() returns for it rather than by
the vendor's own illuminant label. The simple IPA feeds estimateCCT() the raw
channel ratios and then looks the result up in this curve, so keying it the
same way makes the curve an exact identity on a genuinely grey scene and a
pure projection on everything else. A vendor CT label would not do that --
estimateCCT() is not a physical CCT.

Run with no arguments to self-check against the imx362 measurement of record.
"""
import struct
import sys

# .data of the chromatix 3a stub: one PROGBITS section at file offset 0x410.
DATA_OFF = 0x410
REFPOINTS_OFF = 0x30
REFPOINTS_N = 16

# Reference points closer together than this cannot be told apart by
# estimateCCT(); interpolating between them sawtooths, so merge them. 100 K is
# what the CCM algorithm already uses as its "same temperature" threshold.
MERGE_K = 100

# imx362, measured 2026-08-20 on a white reference under a ~3400 K lamp by
# inverting the soft-ISP pipeline (docs/FINDINGS-2026-08-20.md section 16).
IMX362_MEASURED = (0.746, 0.367)


def estimate_cct(rg, bg):
    """libipa ipa::estimateCCT(), src/ipa/libipa/colours.cpp."""
    m = ((-0.14282, 1.54924, -0.95641),
         (-0.32466, 1.57837, -0.73191),
         (-0.68202, 0.77073, 0.56332))
    xyz = [r[0] * rg + r[1] * 1.0 + r[2] * bg for r in m]
    s = sum(xyz)
    x, y = xyz[0] / s, xyz[1] / s
    n = (x - 0.3320) / (0.1858 - y)
    return 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33


def reference_points(path):
    data = open(path, 'rb').read()
    base = DATA_OFF + REFPOINTS_OFF
    return [struct.unpack_from('<ff', data, base + 8 * i)
            for i in range(REFPOINTS_N)]


def locus(points):
    """-> [(ct, gain_r, gain_b)], sorted, near-duplicates merged."""
    uniq = sorted(set(points), key=lambda p: estimate_cct(*p))
    groups, cur = [], [uniq[0]]
    for p in uniq[1:]:
        if estimate_cct(*p) - estimate_cct(*cur[-1]) < MERGE_K:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
    groups.append(cur)
    out = []
    for g in groups:
        rg = sum(p[0] for p in g) / len(g)
        bg = sum(p[1] for p in g) / len(g)
        out.append((int(round(estimate_cct(rg, bg))), 1 / rg, 1 / bg))
    return out


def interpolate(table, ct):
    if ct <= table[0][0]:
        return table[0][1:]
    if ct >= table[-1][0]:
        return table[-1][1:]
    for i in range(1, len(table)):
        if table[i][0] >= ct:
            lo, hi = table[i - 1], table[i]
            t = (ct - lo[0]) / (hi[0] - lo[0])
            return (lo[1] + t * (hi[1] - lo[1]), lo[2] + t * (hi[2] - lo[2]))


def yaml_block(table):
    lines = ["      colourGains:"]
    for ct, gr, gb in table:
        lines.append("        - ct: %d" % ct)
        lines.append("          gains: [ %.4f, %.4f ]" % (gr, gb))
    return "\n".join(lines)


def selfcheck():
    """The decode is only trustworthy if it agrees with a measurement we made
    ourselves. Our white reference must land on the decoded curve."""
    blob = ("blobs/work/chromatix/"
            "libchromatix_imx362_pixel_res0_snapshot_3a.so")
    table = locus(reference_points(blob))
    rg, bg = IMX362_MEASURED
    want = (1 / rg, 1 / bg)
    got = interpolate(table, estimate_cct(rg, bg))
    err = [abs(got[i] / want[i] - 1) for i in (0, 1)]
    print("measured white point R/G %.3f B/G %.3f -> "
          "neutralising gains R %.3f B %.3f" % (rg, bg, want[0], want[1]))
    print("decoded locus at %d K                  -> "
          "gains R %.3f B %.3f  (err %+.1f%% / %+.1f%%)"
          % (estimate_cct(rg, bg), got[0], got[1],
             100 * (got[0] / want[0] - 1), 100 * (got[1] / want[1] - 1)))
    assert max(err) < 0.03, "decoded locus disagrees with the measurement"
    print("\n" + yaml_block(table))
    print("\nOK")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        for ct, gr, gb in locus(reference_points(sys.argv[1])):
            print("%5d  R %.4f  B %.4f" % (ct, gr, gb))
    else:
        selfcheck()
