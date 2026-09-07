#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: PHONE
# exits: 0 ok · non-zero on failure
"""Check that qcom_smgr suppresses the proximity IR crosstalk in the ALS.

Settled, do not re-litigate:

- The channels are NOT swapped. SMGR names data type 0 PROX and 1 ALS on sensor
  0x28, matching the driver's scan_index. Prox and ALS are ONE part sharing ONE
  optical window, and the proximity IR emitter reaches the ALS photodiode.
- It is not a backlight feedback loop. Measured with the backlight pinned:
  124 lux uncovered against 41088 with a fingertip on the glass, 331x.
- **The vendor does NOT cancel this.** The SLPI's knobs for it,
  visible_light_trans_ratio and ir_light_trans_ratio, are both ZERO in the
  device's own factory registry (group 1040, and als_factor in the same group
  IS calibrated, so the registry is real). Android holds the ambient reading in
  the framework instead. qcom_smgr does the same thing a layer down.
- The crosstalk LEADS the proximity trip, so the value held cannot be the last
  sample taken while far -- that one is already raised. The driver holds a
  sliding minimum over far samples.

What this tool still earns its keep for is that none of that is visible from
outside once it works: the driver replaces the contaminated value, so "the lux
never rose" is both what success looks like and what a dead ALS looks like.
The verdict separates them on whether proximity fired, whether the held value
is a plateau, and whether the ALS moved at all while far.

Hovering and touching cannot be told apart after the fact, so the phases are on
a fixed clock announced before it starts. Do not improvise:

    ph-prox-check.py             # ~40 s, follow the countdown
    ph-prox-check.py --selftest  # check the verdict logic, no phone needed
"""
import os
import statistics
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

# (start second, end second, key, instruction).  The gaps between phases are
# deliberate: they are when you move, and their samples are thrown away.
PHASES = [
    (0,  8,  "baseline", "Hands OFF. Leave the phone in normal room light."),
    (10, 18, "hover",    "Hold a hand about 5 cm ABOVE the earpiece.\n"
                         "                 Shade it, but DO NOT TOUCH the glass."),
    (20, 28, "cover",    "Now press a fingertip FLAT on the glass over the\n"
                         "                 earpiece."),
    (30, 38, "after",    "Hands OFF again."),
]
END = 38


def phase_at(t):
    for start, stop, key, _ in PHASES:
        if start <= t < stop:
            return key
    return None


def classify(by_phase, near_frac, near_lux, near_distinct, far_distinct):
    """-> (verdict line, is_a_bug). Split out so --selftest can drive it.

    The discriminator is near_distinct: how many DIFFERENT lux values were seen
    while proximity said near. The freeze replaces the reported value with the
    previous one, so if it is executing, a run of near samples is a plateau --
    a handful of distinct values at most. Dozens of distinct values while near
    means the freeze is not running at all, which is a completely different bug
    from it running and being defeated.
    """
    base = by_phase.get("baseline")
    hover = by_phase.get("hover")
    cover = by_phase.get("cover")
    if base is None or hover is None or cover is None:
        return "not all phases produced samples -- nothing to conclude", False

    # Once the hold works, the contaminated value is invisible from here -- the
    # driver replaces it. So "lux never rose" no longer means "no crosstalk";
    # it is also exactly what success looks like. Separate the two on whether
    # proximity fired and whether the held value is a plateau.
    if near_frac.get("cover", 0) < 0.5:
        return ("proximity never fired while covered, so the sensor was not "
                "actually covered (or prox is broken). Nothing to conclude "
                "about the ALS.", False)

    covered_near = near_lux.get("cover")
    n = near_distinct.get("cover", 0)
    contaminated = covered_near is not None and covered_near > 1.3 * base

    if contaminated and n > 5:
        return (f"proximity says NEAR and the lux still moves freely "
                f"({n} distinct values while near) -- the hold is NOT "
                f"executing. Check that the running module has it before "
                f"looking anywhere else.", True)
    if contaminated:
        return (f"the hold IS executing -- only {n} distinct lux values while "
                f"near, i.e. a plateau -- but it latched a CONTAMINATED value "
                f"({covered_near:.0f} against a {base:.0f} baseline). The "
                f"crosstalk leads the proximity trip, so a sample taken while "
                f"far is already raised; it has to come from before the "
                f"approach began.", True)

    if far_distinct < 2:
        return ("the lux never moved at all, in any phase -- the ALS is not "
                "really reporting. Fix that before reading anything else here.",
                True)

    if n <= 3:
        return (f"HOLDING CORRECTLY. Proximity fired ({near_frac['cover']*100:.0f}% "
                f"of the covered phase) and the reported lux sat on a plateau "
                f"({n} distinct value{'' if n == 1 else 's'}) at "
                f"{covered_near:.0f} against a {base:.0f} baseline, while the "
                f"ALS stayed live elsewhere ({far_distinct} distinct values "
                f"while far). The crosstalk is being suppressed, not missing.",
                False)

    return (f"lux stayed near baseline while covered but moved across {n} values "
            f"-- not a plateau, so this is not the hold working. More likely the "
            f"crosstalk simply did not reproduce this run.", False)


def selftest():
    lots = {"cover": 40}
    few = {"cover": 2}
    one = {"cover": 1}
    hi = {"hover": 0.9, "cover": 1.0}
    # near, lux moving freely, many distinct values -> hold not running
    v, bug = classify({"baseline": 90, "hover": 95, "cover": 2100},
                      hi, {"cover": 2100}, lots, 9)
    assert bug and "NOT" in v and "executing" in v, v
    # near, plateau, but held value is raised -> latched during the approach
    v, bug = classify({"baseline": 90, "hover": 95, "cover": 2100},
                      hi, {"cover": 2100}, few, 9)
    assert bug and "CONTAMINATED" in v, v
    # the success case, which used to be misread as "crosstalk not reproducing"
    v, bug = classify({"baseline": 100, "hover": 76, "cover": 76},
                      {"hover": 0.85, "cover": 1.0}, {"cover": 76}, one, 9)
    assert not bug and "HOLDING CORRECTLY" in v, v
    # a dead ALS looks like success unless the far samples are checked too
    v, bug = classify({"baseline": 76, "hover": 76, "cover": 76},
                      hi, {"cover": 76}, one, 1)
    assert bug and "not really reporting" in v, v
    # never actually covered -> no conclusion, not a pass
    v, bug = classify({"baseline": 90, "hover": 95, "cover": 95},
                      {"hover": 0.0, "cover": 0.0}, {}, {}, 9)
    assert not bug and "never fired" in v, v
    # a missing phase must not produce a verdict
    v, bug = classify({"baseline": 90}, {}, {}, {}, 9)
    assert not bug and "nothing to conclude" in v, v
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()

    dev = subprocess.run(
        SSH + [PHONE, 'for d in /sys/bus/iio/devices/iio:device*; do '
                      'n=$(cat $d/name 2>/dev/null); '
                      'case "$n" in *prox*) echo $d;; esac; done'],
        # stdin=DEVNULL: ssh reads stdin by default and eats the ENTER the
        # phase clock waits for -- and, on a terminal, your keystrokes.
        capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.split()
    if not dev:
        sys.exit("no prox/light device found")
    dev = dev[0]

    print(__doc__.split("Hovering and touching")[0].rstrip())
    print(f"\ndevice: {dev}")
    print("\nThe clock starts when you press ENTER and does not wait for you:")
    for start, stop, key, instr in PHASES:
        print(f"   {start:2d}-{stop:2d}s  {key:<9} {instr.splitlines()[0]}")
    # Pin the backlight. Auto-brightness works now, so an uncontrolled run
    # measures a FEEDBACK LOOP -- the screen's own light reflects off whatever
    # is covering the window, raises the lux, which raises the backlight -- and
    # not the sensor. Pinning it makes the crosstalk a fixed quantity again.
    bl = subprocess.run(
        SSH + [PHONE, "ls -d /sys/class/backlight/* 2>/dev/null | head -1"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    saved = None
    if bl:
        saved = subprocess.run(
            SSH + [PHONE, f"cat {bl}/brightness"], capture_output=True,
            text=True, stdin=subprocess.DEVNULL).stdout.strip()
        subprocess.run(
            SSH + [PHONE, f"gsettings set org.gnome.settings-daemon.plugins."
                          f"power ambient-enabled false 2>/dev/null; "
                          f"echo {saved} | sudo -n tee {bl}/brightness"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        print(f"\nbacklight pinned at {saved}/"
              f"{subprocess.run(SSH + [PHONE, f'cat {bl}/max_brightness'], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()}"
              " and auto-brightness disabled for the run")

    print("\nPress ENTER to start.", end=" ")
    input()

    # `timeout` on the device side, so the stream always ends even if this end
    # dies; each sample costs two QMI round trips, which is its own rate limit.
    proc = subprocess.Popen(
        SSH + [PHONE, f"timeout {END + 5} sh -c 'while :; do "
                      f"echo \"$(cat {dev}/in_proximity_raw) "
                      f"$(cat {dev}/in_illuminance_raw) "
                      f"$(cat {bl}/brightness 2>/dev/null || echo -1)\"; done'"],
        stdout=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)

    samples = []
    t0 = time.time()
    shown = object()
    try:
        # Blocking line reads, clock checked per line. Non-blocking reads on a
        # TextIOWrapper are not reliable -- readline() can raise or hand back a
        # partial line -- and the phases only need ~tenth-second resolution.
        for line in proc.stdout:
            t = time.time() - t0
            if t >= END:
                break
            key = phase_at(t)
            if key != shown:
                shown = key
                if key:
                    instr = next(i for s, e, k, i in PHASES if k == key)
                    print(f"\n  [{int(t):2d}s] {key.upper()}: {instr}")
                else:
                    print("\n  ... move now ...")
            parts = line.split()
            if key and len(parts) == 3 and all(p.lstrip('-').isdigit()
                                               for p in parts):
                samples.append((key, int(parts[0]), int(parts[1]),
                                int(parts[2])))
            print(f"      {END - t:4.0f}s left, {len(samples):4d} samples",
                  end="\r", flush=True)
    finally:
        proc.kill()
    print(" " * 70, end="\r")

    if not samples:
        sys.exit("no samples came back -- is the prox/light sensor reporting?")

    by_phase, near_frac, near_lux, near_distinct = {}, {}, {}, {}
    print(f"\n{len(samples)} samples\n")
    print(f"  {'phase':<10}{'n':>5}{'lux med':>10}{'prox NEAR':>11}"
          f"{'lux WHILE NEAR':>16}{'distinct':>9}{'lux far':>10}"
          f"{'backlight':>14}")
    for _, _, key, _ in PHASES:
        rows = [(p, l, b) for k, p, l, b in samples if k == key]
        if not rows:
            print(f"  {key:<10}    0   (no samples)")
            continue
        lux = [l for _, l, _ in rows]
        nl = [l for p, l, _ in rows if p > 0]
        fl = [l for p, l, _ in rows if p == 0]
        bri = [b for _, _, b in rows]
        by_phase[key] = statistics.median(lux)
        near_frac[key] = len(nl) / len(rows)
        if nl:
            near_lux[key] = statistics.median(nl)
            near_distinct[key] = len(set(nl))
        print(f"  {key:<10}{len(rows):5d}{by_phase[key]:10.0f}"
              f"{near_frac[key] * 100:10.0f}%"
              f"{(f'{near_lux[key]:.0f}' if nl else '-'):>16}"
              f"{(str(near_distinct[key]) if nl else '-'):>9}"
              f"{(f'{statistics.median(fl):.0f}' if fl else '-'):>10}"
              f"{min(bri):9d}..{max(bri):<4d}")

    far_distinct = len({l for _, _, l, _ in
                        [(k, p, l, b) for k, p, l, b in samples if p == 0]})
    verdict, is_bug = classify(by_phase, near_frac, near_lux, near_distinct,
                               far_distinct)
    print(f"\n  VERDICT: {verdict}")

    if bl and saved:
        subprocess.run(
            SSH + [PHONE, "gsettings set org.gnome.settings-daemon.plugins."
                          "power ambient-enabled true 2>/dev/null"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        print("\n  auto-brightness re-enabled")
    return 1 if is_bug else 0


if __name__ == "__main__":
    sys.exit(main())
