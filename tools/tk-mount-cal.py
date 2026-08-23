#!/usr/bin/env python3
# scope: generic
# needs: BOOTED
# env: PHONE
# exits: 0 ok · 1 failed
"""Measure the accelerometer mount matrix from four guided poses.

Why this exists: the tool this replaces (tk-orient-check.py, deleted) only scored two candidate matrices, it
timed the poses instead of asking for them, and its third pose was described as
"turned 90 degrees clockwise" -- which is ambiguous (clockwise seen from where?)
and is exactly where a 90 degree rotation error hides. This one names each pose
by which physical EDGE points at the CEILING, which cannot be read two ways,
waits for you to be in it, and derives the whole matrix from the data instead
of picking between guesses.

Frame conventions
-----------------
Device frame (what IIO, iio-sensor-proxy and phosh all expect), looking at the
screen the normal way up:

    +x = towards the RIGHT edge
    +y = towards the TOP edge (earpiece / front camera)
    +z = out of the screen, towards your face

An accelerometer at rest reports the reaction to gravity, so it reads +1 g
along whichever axis points at the CEILING. That is the whole measurement:
each pose puts a known device axis at the ceiling, so whatever SMGR reports in
that pose IS that axis, expressed in SMGR's own frame.

The poses name edges by the USB-C port, and the in-plane ones are confirmed
against the PROXIMITY sensor beside the earpiece. Without that landmark a
phone held consistently upside down produces a consistent, self-checking,
determinant +1 matrix that is 180 degrees wrong -- which is what happened.

Three poses fix the matrix with no freedom left. The fourth is a check on the
x axis, which is the one in dispute.

    tk-mount-cal.py            # run the calibration
    tk-mount-cal.py --selftest # check the solver, no phone needed

PHONE=user@host overrides the ssh target.
"""
import os
import subprocess
import statistics
import sys

# Locate lib/porthole.py by walking up, so this works both from tools/ and
# from profiles/<device>/tools/ without either hardcoding a depth.
import pathlib as _pl, sys as _sys
for _p in _pl.Path(__file__).resolve().parents:
    if (_p / "lib" / "porthole.py").is_file():
        _sys.path.insert(0, str(_p / "lib"))
        break
import porthole

PHONE = porthole.resolve_phone(porthole.load_config())
SSH = ["ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
       "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
       "-o", "BatchMode=yes"]
Q = 1 << 16
NSAMP = 10
UNIT = "tk-mount-cal"
LOG = "/tmp/tk-mount-cal.log"

AXES = "xyz"

# (prompt, device-frame unit vector pointing at the ceiling, confirm with prox)
#
# The edges are named by the USB-C port, which nobody can misidentify, and the
# in-plane poses are confirmed against the PROXIMITY sensor: it sits beside the
# earpiece, at the top of the phone. Asking for a fingertip there and requiring
# proximity to fire ties the accelerometer reading to a physical landmark,
# instead of to whichever end the person holding it believes is the top.
#
# That check is here because its absence cost a whole fix cycle: the first
# measurement was taken with the phone held upside down for poses 2-4, which
# inverts x and y together and is invisible in pose 1. The derived matrix was a
# clean determinant +1 rotation and passed the pose-4 self-check -- a
# consistently mis-held phone is consistent, so nothing internal can catch it.
POSES = [
    ("Lay the phone FLAT on a table, SCREEN facing the ceiling.",
     (0, 0, +1), False),
    ("Stand it UPRIGHT, screen facing you, USB-C port at the BOTTOM.\n"
     "     Rest a fingertip on the earpiece slot at the top of the screen.",
     (0, +1, 0), True),
    ("Screen still facing you, turn it so the USB-C port is at YOUR LEFT\n"
     "     (so the earpiece end points to your right). Keep the fingertip\n"
     "     on the earpiece.",
     (-1, 0, 0), True),
    ("Turn it so the USB-C port is at YOUR RIGHT (earpiece end points to\n"
     "     your left), fingertip still on the earpiece.  (this is the check)",
     (+1, 0, 0), True),
]


def sh(cmd):
    # stdin=DEVNULL: ssh reads stdin by default and would eat the ENTER each
    # pose waits for, along with anything else typed at the terminal.
    return subprocess.run(SSH + [PHONE, cmd], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


def find_dev(match, what):
    out = sh(f'for d in /sys/bus/iio/devices/iio:device*; do '
             f'n=$(cat $d/name 2>/dev/null); '
             f'case "$n" in *{match}*) echo $d;; esac; done').stdout.split()
    if not out:
        sys.exit(f"no {what} found -- is qcom_smgr loaded?")
    return out[0]


def prox_near(dev):
    """True if something is against the earpiece window."""
    r = sh(f"cat {dev}/in_proximity_raw").stdout.strip()
    return r.isdigit() and int(r) > 0


def orientation():
    """What iio-sensor-proxy says RIGHT NOW.

    Read the property rather than tailing monitor-sensor: monitor-sensor only
    prints on CHANGE, so a tail of its log lags a pose behind and reports the
    previous one, which is its own way of manufacturing a 90 degree error.
    monitor-sensor still has to be running, because the proxy only polls a
    sensor while something holds a claim.
    """
    out = sh("sudo -n busctl get-property net.hadess.SensorProxy "
             "/net/hadess/SensorProxy net.hadess.SensorProxy "
             "AccelerometerOrientation").stdout.strip()
    return out.replace('s ', '').strip('" ')


def sample(dev):
    """NSAMP reads of all three axes; returns (mean g vector, worst stdev)."""
    files = " ".join(f"{dev}/in_accel_{a}_raw" for a in AXES)
    out = sh(f"for i in $(seq {NSAMP}); do cat {files}; done").stdout.split()
    vals = [int(v) / Q for v in out if v.lstrip("-").isdigit()]
    rows = [vals[i:i + 3] for i in range(0, len(vals) - 2, 3)]
    if len(rows) < 3:
        sys.exit(f"only {len(rows)} samples came back -- sensor stalled?")
    mean = [statistics.fmean(r[k] for r in rows) for k in range(3)]
    jitter = max(statistics.pstdev([r[k] for r in rows]) for k in range(3))
    return mean, jitter


def dominant(v):
    """(axis index, sign) of the clearly dominant component, else None."""
    i = max(range(3), key=lambda k: abs(v[k]))
    others = max(abs(v[k]) for k in range(3) if k != i)
    if abs(v[i]) < 7.0 or others > 0.35 * abs(v[i]):
        return None
    return i, (1 if v[i] > 0 else -1)


def solve(observed):
    """observed: [(smgr axis, sign, device-frame ceiling vector)] -> 3x3 matrix.

    M maps an SMGR vector to the device frame.  If in some pose SMGR reads
    s * e_j and the device axis at the ceiling is e_k, then M @ (s e_j) = e_k,
    so column j of M is s * e_k -- i.e. M[k][j] = s and the rest of that
    column is zero.
    """
    m = [[0] * 3 for _ in range(3)]
    for j, s, up in observed:
        k = max(range(3), key=lambda t: abs(up[t]))
        m[k][j] = s * (1 if up[k] > 0 else -1)
    return m


def apply(m, v):
    return [sum(m[r][c] * v[c] for c in range(3)) for r in range(3)]


def fmt(m):
    return "; ".join(", ".join(f"{m[r][c]:d}" for c in range(3))
                     for r in range(3))


def det(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def selftest():
    # SMGR frame = (top, right, into-the-screen): the "x and y swapped" case
    # the old tool could not distinguish from a bad pose ordering.
    truth = [[0, 1, 0], [1, 0, 0], [0, 0, -1]]
    inv = truth          # a signed permutation of this shape is its own inverse
    obs = []
    for _, up, _ in POSES[:3]:
        smgr = apply(inv, [9.81 * c for c in up])
        d = dominant(smgr)
        assert d, f"synthetic pose not dominant: {smgr}"
        obs.append((d[0], d[1], up))
    got = solve(obs)
    assert got == truth, f"solver returned {got}, want {truth}"
    # and the check pose must agree
    smgr = apply(inv, [9.81 * c for c in POSES[3][1]])
    pred = apply(got, smgr)
    assert dominant(pred)[0] == 0 and dominant(pred)[1] == +1, pred
    assert det(got) == det(truth)
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()

    dev = find_dev("accel", "accelerometer")
    prox = find_dev("prox", "proximity sensor")
    # IIO_SHARED_BY_DIR names it in_mount_matrix, not in_accel_mount_matrix
    current = sh(f"cat {dev}/in_mount_matrix 2>/dev/null").stdout.strip()
    print(__doc__.split("Three poses fix")[0].rstrip())
    print(f"\naccelerometer: {dev}   proximity: {prox}")
    print(f"matrix on the device now: {current or '(none)'}")

    have_proxy = sh(f"sudo -n systemd-run --unit={UNIT} --collect "
                    f"sh -c 'timeout 900 monitor-sensor > {LOG} 2>&1'").returncode == 0
    if not have_proxy:
        print("(monitor-sensor could not be started -- orientation column will "
              "be blank)")

    try:
        results = []
        for n, (prompt, up, need_prox) in enumerate(POSES, 1):
            print(f"\n  Pose {n}/4: {prompt}")
            print("     Hold it still, then press ENTER.", end=" ")
            input()
            v, jitter = sample(dev)
            mag = sum(c * c for c in v) ** 0.5
            near = prox_near(prox)
            orient = orientation() if have_proxy else ""
            print(f"     smgr = ({v[0]:+6.2f},{v[1]:+6.2f},{v[2]:+6.2f}) m/s^2"
                  f"   |a| = {mag:5.2f}   jitter = {jitter:4.2f}"
                  f"   prox {'NEAR' if near else 'far'}"
                  + (f"   proxy says '{orient}'" if orient else ""))
            if need_prox and not near:
                print("     Proximity did not fire, so I cannot tell which end\n"
                      "     of the phone is which -- and guessing that is what\n"
                      "     produced the last wrong matrix. Either the fingertip\n"
                      "     is not on the earpiece slot, or the end you are\n"
                      "     treating as the top is actually the USB-C end.\n"
                      "     Check the port, put a fingertip on the earpiece, and\n"
                      "     start again.")
                return 1
            if not 8.4 < mag < 11.2:
                print(f"     |a| is {mag:.2f}, not ~9.8 -- the phone was moving,"
                      " or the sensor is lying. Start again.")
                return 1
            if jitter > 0.6:
                print(f"     too much jitter ({jitter:.2f}) -- hold it steadier"
                      " and start again.")
                return 1
            d = dominant(v)
            if d is None:
                print("     no axis dominates -- the phone is not squarely in "
                      "that pose. Start again.")
                return 1
            results.append((d[0], d[1], up, v, orient))

        print("\n== what each pose says ==")
        for n, (j, s, up, v, _) in enumerate(results, 1):
            k = max(range(3), key=lambda t: abs(up[t]))
            print(f"  pose {n}: ceiling is device {'+' if up[k] > 0 else '-'}"
                  f"{AXES[k]}, smgr reads {'+' if s > 0 else '-'}{AXES[j]}"
                  f"   =>  smgr {AXES[j]} is the device's "
                  f"{'' if s * up[k] > 0 else 'negated '}{AXES[k]} axis")

        cols = {r[0] for r in results[:3]}
        if len(cols) != 3:
            print("\n  Poses 1-3 did not land on three different SMGR axes "
                  f"({sorted(AXES[c] for c in cols)}). Something is wrong with "
                  "the poses or the sensor -- nothing can be derived.")
            return 1

        m = solve([(j, s, up) for j, s, up, _, _ in results[:3]])
        check = apply(m, results[3][3])
        want = results[3][2]
        ok = dominant(check) == (max(range(3), key=lambda t: abs(want[t])),
                                 1 if want[0] > 0 else -1)

        print("\n== measured mount matrix ==")
        print(f"  {fmt(m)}        determinant {det(m):+d}")
        print(f"  device now:   {current}")
        print(f"  {'unchanged -- the matrix is already right' if current.replace(' ', '') == fmt(m).replace(' ', '') else 'DIFFERENT -- this is the fix'}")
        print(f"\n  pose 4 check: matrix maps it to "
              f"({check[0]:+5.2f},{check[1]:+5.2f},{check[2]:+5.2f}), "
              f"want gravity along {'+' if want[0] > 0 else '-'}x   "
              f"{'ok' if ok else 'FAILED -- do not trust this result'}")

        print("\n== paste into qcom_smgr.c ==")
        rows = fmt(m).split("; ")
        print("\t.rotation = {")
        for i, row in enumerate(rows):
            cells = ", ".join(f'"{c.strip()}"' for c in row.split(","))
            print(f"\t\t{cells}" + ("," if i < 2 else ""))
        print("\t}")
        return 0 if ok else 1
    finally:
        if have_proxy:
            sh(f"sudo -n systemctl stop {UNIT} 2>/dev/null")


if __name__ == "__main__":
    sys.exit(main())
