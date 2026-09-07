#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: soc:qcom
# needs: BOOTED
# env: PHONE
# exits: 0 ok · 1 failed
"""Calibrate the magnetometer's hard-iron offset, and persist it.

Uncalibrated, taimen's magnetometer reads a field magnitude of about 1.58
Gauss. The earth's is 0.25-0.65. The difference is the phone's own magnets --
speaker, vibrator, the case -- adding a fixed vector in the device frame, and
it is far larger than the field being measured, so an uncorrected heading is
not slightly wrong, it is meaningless.

The offset is the centre of the sphere the readings trace out as the phone is
turned. Sampling while it is rotated through as many orientations as possible
and taking the midpoint of each axis' range recovers it (hard-iron only; soft
iron would need an ellipsoid fit, and is usually a smaller effect).

    ph-compass-cal.py [seconds]     # default 40

Rotate the phone slowly through every orientation you can for the whole window
-- the usual figure-of-eight, plus a full spin about each of the three axes.
Coverage is what decides the quality, so the tool reports it rather than just
claiming success.
"""
import os
import subprocess
import sys
import time

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
AXES = ("x", "y", "z")


def sh(cmd):
    return subprocess.run(SSH + [PHONE, cmd], capture_output=True, text=True)


def find_mag():
    out = sh('for d in /sys/bus/iio/devices/iio:device*; do '
             'n=$(cat $d/name 2>/dev/null); '
             'case "$n" in *mag*) echo $d;; esac; done').stdout.split()
    if not out:
        sys.exit("no magnetometer found")
    return out[0]


def sample(dev, secs):
    cmd = ("cat " + " ".join(f"{dev}/in_magn_{a}_raw" for a in AXES))
    pts, end = [], time.time() + secs
    while time.time() < end:
        r = sh(cmd).stdout.split()
        if len(r) == 3:
            try:
                pts.append([int(v) for v in r])
            except ValueError:
                pass
        left = end - time.time()
        print(f"  sampling, {left:4.0f}s left, {len(pts):4d} points", end="\r",
              flush=True)
    print(" " * 60, end="\r")
    return pts


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    dev = find_mag()
    print(f"magnetometer: {dev}")

    # Start from zero, or the reading is already offset by the last calibration
    for i, a in enumerate(AXES):
        sh(f"echo 0 | sudo tee {dev}/in_magn_{a}_calibbias >/dev/null")

    print(f"\nRotate the phone slowly through EVERY orientation for {secs}s:")
    print("  a figure-of-eight, then a full turn about each of its three axes.\n")
    for t in range(3, 0, -1):
        print(f"  starting in {t}s ", end="\r", flush=True)
        time.sleep(1)

    pts = sample(dev, secs)
    if len(pts) < 50:
        sys.exit(f"only {len(pts)} samples -- too few to fit anything")

    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    bias = [(lo[i] + hi[i]) // 2 for i in range(3)]
    span = [(hi[i] - lo[i]) / 2 for i in range(3)]

    print(f"{len(pts)} samples\n")
    print("  axis      min        max       bias    half-span (should match)")
    for i, a in enumerate(AXES):
        print(f"   {a}   {lo[i]:9d} {hi[i]:10d} {bias[i]:10d} {span[i]:12.0f}")

    # After removing the offset every reading should sit on one sphere, so the
    # three half-spans should agree. If they do not, the phone was not turned
    # through enough orientations -- or there is real soft-iron distortion.
    best, worst = min(span), max(span)
    cover = best / worst if worst else 0
    field = sum(span) / 3 / Q
    print(f"\n  coverage {cover * 100:.0f}%  (half-spans agree this well)")
    print(f"  field magnitude {field:.2f} G -- the earth's is 0.25-0.65")

    if cover < 0.6:
        print("\n  NOT APPLIED: the axes disagree too much, which means the phone\n"
              "  was not turned through enough orientations. Run it again and\n"
              "  keep rotating for the whole window.")
        return 1
    if not 0.15 < field < 1.0:
        print(f"\n  NOT APPLIED: {field:.2f} G is not a plausible earth field.\n"
              "  Something magnetic is next to the phone, or the scale is wrong.")
        return 1

    for i, a in enumerate(AXES):
        sh(f"echo {bias[i]} | sudo tee {dev}/in_magn_{a}_calibbias >/dev/null")
    print("\n  applied to the running driver.")

    rule = ('ACTION=="add", SUBSYSTEM=="iio", ATTR{name}=="qcom-smgr-mag", '
            + ", ".join(f'ATTR{{in_magn_{a}_calibbias}}="{bias[i]}"'
                        for i, a in enumerate(AXES)))
    print("\nTo persist it, add this to\n"
          "/usr/lib/udev/rules.d/99-taimen-compass-cal.rules :\n")
    print("  " + rule)
    print("\n(calibbias is not stored on the device -- it is a driver setting,\n"
          " so it has to be re-applied on every boot.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
