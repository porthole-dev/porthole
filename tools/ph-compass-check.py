#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok
"""Check the compass maths in qcom_smgr.c against floating point.

The driver has to compute the heading in Q16 integer arithmetic -- there is no
FPU in the kernel -- so the CORDIC table, the shifts and the sign conventions
are all places a silent error hides. This reimplements the *same* integer
algorithm and compares it against a float reference over the whole sphere of
orientations.

It does not test the C. It tests the algorithm the C transcribes, which is the
part that cannot be checked by looking at a compass and squinting.

    ph-compass-check.py            # run the checks
"""
import math
import random
import sys

Q = 1 << 16

# atan(2^-i) in Q16 degrees -- must match qcom_smgr_cordic_atan[]
ATAN = [2949120, 1740993, 919938, 466945, 234378, 117308, 58666, 29335,
        14667, 7334, 3667, 1834, 917, 458, 229, 115]


def isqrt64(v):
    return math.isqrt(v)


def cdiv(a, b):
    """C integer division: truncates toward zero, unlike Python's //."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def atan2_q16(y, x):
    """CORDIC vectoring, mirroring qcom_smgr_atan2(). Q16 degrees in [0,360)."""
    if x == 0 and y == 0:
        return 0
    z = 0
    if x < 0:
        x, y, z = -x, -y, 180 * Q
    for i, a in enumerate(ATAN):
        if y > 0:
            x, y, z = x + (y >> i), y - (x >> i), z + a
        else:
            x, y, z = x - (y >> i), y + (x >> i), z - a
    while z < 0:
        z += 360 * Q
    while z >= 360 * Q:
        z -= 360 * Q
    return z


def to_iio(v):
    """(x, y, z)_iio = (x, y, -z)_smgr -- the driver's mount matrix, measured."""
    return [v[0], v[1], -v[2]]


def heading_q16(accel_smgr, mag_smgr):
    """Mirrors qcom_smgr_heading()."""
    a = to_iio(accel_smgr)
    m = to_iio(mag_smgr)

    norm = isqrt64(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    if norm == 0:
        return 0
    up = [cdiv(a[i] * Q, norm) for i in range(3)]

    dot = (m[0] * up[0] + m[1] * up[1] + m[2] * up[2]) >> 16
    nh = [m[i] - ((dot * up[i]) >> 16) for i in range(3)]
    ey = (nh[2] * up[0] - nh[0] * up[2]) >> 16
    return atan2_q16(ey, nh[1])


def heading_float(accel_smgr, mag_smgr):
    """Float reference, same geometry, no fixed point anywhere."""
    a = [float(v) for v in to_iio(accel_smgr)]
    m = [float(v) for v in to_iio(mag_smgr)]
    n = math.sqrt(sum(v * v for v in a))
    up = [v / n for v in a]
    dot = sum(m[i] * up[i] for i in range(3))
    nh = [m[i] - dot * up[i] for i in range(3)]
    ey = nh[2] * up[0] - nh[0] * up[2]
    return math.degrees(math.atan2(ey, nh[1])) % 360.0


def angdiff(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _rodrigues(v, axis, ang):
    """Rotate v about a unit axis by ang (right-handed, radians)."""
    c, s = math.cos(ang), math.sin(ang)
    dot = sum(v[i] * axis[i] for i in range(3))
    cx = [axis[1] * v[2] - axis[2] * v[1],
          axis[2] * v[0] - axis[0] * v[2],
          axis[0] * v[1] - axis[1] * v[0]]
    return [v[i] * c + cx[i] * s + axis[i] * dot * (1 - c) for i in range(3)]


def synth(heading_deg, roll_deg, pitch_deg, inclination_deg=60.0):
    """Build accel+mag SMGR samples for a known orientation.

    The device basis is built explicitly in world ENU coordinates rather than
    by composing elementary rotation matrices -- the composition order and the
    sign of each angle are exactly the conventions that are easy to get wrong,
    and getting them wrong produces a plausible-looking error rather than an
    obvious one.

    Yaw is applied about world up, then pitch about the device's own x, then
    roll about its own y. That order leaves the azimuth of the device y axis
    equal to `heading_deg`, which is what the driver computes.
    """
    E, N, U = [1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]

    # device axes, starting aligned with world: x = east, y = north, z = up
    xd, yd, zd = E[:], N[:], U[:]

    # yaw: clockwise from north seen from above, so negative about +U
    yaw = math.radians(-heading_deg)
    xd, yd, zd = (_rodrigues(v, U, yaw) for v in (xd, yd, zd))

    # pitch about the device x axis, then roll about the device y axis
    pitch = math.radians(pitch_deg)
    ax = xd[:]
    xd, yd, zd = (_rodrigues(v, ax, pitch) for v in (xd, yd, zd))

    roll = math.radians(roll_deg)
    ay = yd[:]
    xd, yd, zd = (_rodrigues(v, ay, roll) for v in (xd, yd, zd))

    def to_dev(v):
        return [sum(v[i] * a[i] for i in range(3)) for a in (xd, yd, zd)]

    inc = math.radians(inclination_deg)
    mag_w = [0.0, math.cos(inc), -math.sin(inc)]   # north and downward

    up_d, mag_d = to_dev(U), to_dev(mag_w)

    # device frame -> SMGR frame is the inverse of (x, y, -z), i.e. itself
    def to_smgr(v):
        return [int(round(v[0] * Q)), int(round(v[1] * Q)),
                int(round(-v[2] * Q))]

    return to_smgr(up_d), to_smgr(mag_d)


def demo():
    random.seed(1)

    # 1. the CORDIC alone
    worst = 0.0
    for _ in range(20000):
        x = random.randint(-(1 << 24), 1 << 24)
        y = random.randint(-(1 << 24), 1 << 24)
        got = atan2_q16(y, x) / Q
        want = math.degrees(math.atan2(y, x)) % 360.0
        worst = max(worst, angdiff(got, want))
    assert worst < 0.02, f"atan2 off by {worst:.4f} deg"
    print(f"  atan2 vs libm over 20000 points: worst {worst:.4f} deg")

    # 2. fixed point vs float, same geometry
    worst = 0.0
    for _ in range(20000):
        a, m = synth(random.uniform(0, 360), random.uniform(-60, 60),
                     random.uniform(-60, 60))
        worst = max(worst, angdiff(heading_q16(a, m) / Q,
                                   heading_float(a, m)))
    assert worst < 0.5, f"fixed point off by {worst:.4f} deg"
    print(f"  Q16 vs float over 20000 orientations: worst {worst:.4f} deg")

    # 3. does it actually recover the heading it was built from?
    worst = 0.0
    for hdg in range(0, 360, 5):
        for roll, pitch in ((0, 0), (25, 0), (0, 25), (-30, 20), (40, -35)):
            a, m = synth(hdg, roll, pitch)
            worst = max(worst, angdiff(heading_q16(a, m) / Q, hdg))
    assert worst < 1.0, f"recovered heading off by {worst:.4f} deg"
    print(f"  recovered heading, tilts to +/-40 deg: worst {worst:.4f} deg")

    # 4. flat and pointing north must be 0, and the cardinal points must land
    for hdg in (0, 90, 180, 270):
        a, m = synth(hdg, 0, 0)
        got = heading_q16(a, m) / Q
        assert angdiff(got, hdg) < 0.5, f"{hdg} deg read as {got:.2f}"
    print("  cardinal points flat: ok")

    print("demo ok")


if __name__ == "__main__":
    demo()
