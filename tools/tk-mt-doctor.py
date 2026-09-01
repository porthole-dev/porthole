#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as root (any MT protocol-B touchscreen)
# env: -
# exits: 0 no stuck contacts · 2 stuck contacts found (or released)
"""Diagnose and clear phantom touch contacts.

When a finger's LEAVE event is lost (controller FIFO overflow during an
event storm, firmware hiccup), the input stack believes a finger is still on
the glass forever: every new single-finger drag becomes a two-finger gesture
(scroll turns into pinch-zoom), and shell edge gestures stop triggering.
Presents as "the touchscreen went crazy after I pinch-zoomed" -- taimen
2026-09-01, two phantom slots parked mid-screen.

  tk-mt-doctor.py [/dev/input/eventN]            list live slots
  tk-mt-doctor.py [/dev/input/eventN] --release  inject LEAVEs for stuck slots

Injected releases go through the input core, so libinput/compositor state
clears too. The underlying loss is a driver gap: the vendor ftm4 driver
flushes the controller FIFO and force-releases all fingers on error events,
mainline ftm4 only dev_dbg()s them -- arm dynamic debug on ftm4.c to catch
the next occurrence with evidence.
"""
import array
import fcntl
import struct
import sys

ABS_MT_SLOT, ABS_MT_TRACKING_ID = 0x2f, 0x39
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dev = args[0] if args else "/dev/input/event4"
    release = "--release" in sys.argv

    f = open(dev, "rb")
    info = array.array("i", [0] * 6)
    fcntl.ioctl(f, 0x80184540 + ABS_MT_SLOT, info)   # EVIOCGABS
    nslots = info[2] + 1

    def slots(code):
        a = array.array("i", [code] + [0] * nslots)
        fcntl.ioctl(f, (2 << 30) | (((nslots + 1) * 4) << 16) | (ord("E") << 8) | 0x0a, a)
        return list(a)[1:]

    tid = slots(ABS_MT_TRACKING_ID)
    x, y = slots(ABS_MT_POSITION_X), slots(ABS_MT_POSITION_Y)
    stuck = [i for i, t in enumerate(tid) if t != -1]
    for i in stuck:
        print(f"slot {i}: tracking_id={tid[i]} at ({x[i]},{y[i]})  ACTIVE")
    if not stuck:
        print(f"{dev}: no active contacts")
        return 0

    if release:
        ev = lambda t, c, v: struct.pack("llHHi", 0, 0, t, c, v)
        with open(dev, "wb", buffering=0) as out:
            for i in stuck:
                out.write(ev(3, ABS_MT_SLOT, i) + ev(3, ABS_MT_TRACKING_ID, -1)
                          + ev(0, 0, 0))
                print(f"released slot {i}")
    else:
        print("a REAL finger on the glass also lists here -- lift it and "
              "re-run before calling these stuck; --release clears them")
    return 2

if __name__ == "__main__":
    sys.exit(main())
