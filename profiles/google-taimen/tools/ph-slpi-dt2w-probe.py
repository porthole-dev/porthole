#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: on-device as root
# env: -
# exits: 0 the SLPI reported a double tap · 1 handed over, nothing reported · 65 nobody came · 70 setup failed
"""Does the SLPI report a double tap once it owns the touch i2c?

This is the vendor's screen-off path, not ours. fts_stop_device() +
fts_suspend() hand the controller to the SLPI on LCD off, and the SLPI's own
ftm4 driver arms the wake gesture and reports it through the sensor manager --
the AP never arms anything. So the thing to watch is qcom-smgr-double-tap, and
the AP's own interrupt is the control: once the bus is handed over it must go
QUIET.

TWO PHASES, because with the bus handed over there is no way to tell "the SLPI
said nothing" from "nobody touched the glass" -- the AP is deliberately deaf.
Phase 1 keeps the bus and waits for a touch, which proves a finger is present
and starts the timed phase 2. Nothing here announces a clock time: four earlier
runs measured an untouched screen because the operator was not watching the
terminal at the second.

The handover is ALWAYS put back, including on an exception, because leaving it
set is leaving the phone with a dead touchscreen.
"""
import glob, os, select, struct, sys, time

FMT, DUR = "llHHi", int(sys.argv[1]) if len(sys.argv) > 1 else 300
SZ = struct.calcsize(FMT)
HANDOVER = "/sys/module/ftm4/parameters/handover"


def say(*a):
    print(*a, flush=True)


def irq():
    for line in open("/proc/interrupts"):
        if line.rstrip().endswith("ftm4"):
            return int(line.split()[1])
    return -1


def gesture_enable_path():
    for d in glob.glob("/sys/bus/iio/devices/iio:device*"):
        try:
            if open(os.path.join(d, "name")).read().strip() == "qcom-smgr-double-tap":
                return os.path.join(d, "events/in_index0_change_en")
        except OSError:
            pass
    return None


def watch(fds, seconds, want):
    """Return the first event seen on a device whose name contains `want`."""
    end = time.time() + seconds
    while time.time() < end:
        for fd in select.select(list(fds), [], [], 0.5)[0]:
            data = os.read(fd, SZ * 32)
            for i in range(0, len(data) - SZ + 1, SZ):
                _, _, typ, code, val = struct.unpack(FMT, data[i:i + SZ])
                if typ and want in fds[fd]:
                    return f"{fds[fd]} type={typ} code={code} value={val}"
    return None


def main():
    en = gesture_enable_path()
    if not en:
        say("no qcom-smgr-double-tap iio device -- is 0244 in this kernel?")
        return 70
    if not os.path.exists(HANDOVER):
        say("no ftm4.handover -- is 0246 in this kernel?")
        return 70

    fds = {}
    for d in glob.glob("/sys/class/input/input*"):
        try:
            name = open(os.path.join(d, "name")).read().strip()
        except OSError:
            continue
        if "smgr" in name or "ftm4" in name:
            for ev in glob.glob(os.path.join(d, "event*")):
                try:
                    fds[os.open("/dev/input/" + os.path.basename(ev),
                                os.O_RDONLY | os.O_NONBLOCK)] = name
                except OSError as e:
                    say(f"SKIP {ev}: {e}")
    if not any("ftm4" in n for n in fds.values()):
        say("no ftm4 input device to use as the control")
        return 70

    say("PHASE1 touch the screen once -- the bus is still ours, this only")
    say("       proves a finger is present and starts the real phase")
    if not watch(fds, DUR, "ftm4"):
        say("nobody touched the glass")
        return 65
    say(f"FINGER seen, ap_ftm4_irq={irq()}")

    try:
        open(HANDOVER, "w").write("1")
        open(en, "w").write("1")
        before = irq()
        say(f"ARMED handover=1 gesture_en=1 ap_ftm4_irq={before}")
        say("       now DOUBLE-TAP the middle of the screen, repeatedly")
        hit = watch(fds, DUR, "smgr")
        after = irq()
        # -1 means the line is gone from /proc/interrupts entirely, which is a
        # STRONGER control than a quiet count: the AP has released the request,
        # not merely masked it, which is what the SLPI needs in order to
        # register its own handler on the same pin.
        if after == -1:
            note = "  (the AP has RELEASED the line, not just masked it)"
        elif before == after:
            note = "  (quiet, so the handover took)"
        else:
            note = "  (NOT quiet -- the handover did not take)"
        say(f"ap_ftm4_irq {before} -> {after}{note}")
        if hit:
            say(f"RESULT the SLPI reported it: {hit}")
            return 0
        say("RESULT handed over, gesture enabled, and the SLPI reported nothing")
        return 1
    finally:
        try:
            open(en, "w").write("0")
        except OSError as e:
            say(f"could not disable the gesture: {e}")
        try:
            open(HANDOVER, "w").write("0")
            say(f"handover restored to {open(HANDOVER).read().strip()}")
        except OSError as e:
            say(f"COULD NOT RESTORE THE HANDOVER ({e}) -- touch is dead until"
                f" `echo 0 > {HANDOVER}`")


if __name__ == "__main__":
    sys.exit(main())
