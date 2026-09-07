#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
"""Inject touch gestures through /dev/uinput. Run ON THE DEVICE as root.

An untouched screen is indistinguishable from a broken one, and a human finger
is not a repeatable stimulus -- a scroll test is only worth something if the
same swipe can be replayed at the same speed. This makes the phosh session
move on demand so frame rate can be measured against a known input.

Stdlib only (ctypes-free: plain ioctl + struct).

  ph-touch.py swipe X1 Y1 X2 Y2 [MS]     one finger, MS milliseconds (default 300)
  ph-touch.py fling  X1 Y1 X2 Y2 [MS]    swipe that lifts while still moving
  ph-touch.py tap    X Y
  ph-touch.py hold   SECONDS             keep the device alive, do nothing

Coordinates are panel pixels: 1440x2880, origin top-left.
"""
import fcntl
import os
import struct
import sys
import time

UINPUT = "/dev/uinput"
W, H = 1440, 2880

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
BTN_TOUCH = 0x14A
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36
ABS_MT_TRACKING_ID = 0x39
INPUT_PROP_DIRECT = 0x01

UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_SET_ABSBIT, UI_SET_PROPBIT = 0x40045567, 0x4004556E

ABS_CNT = 64
EV_FMT = "@llHHi"
# 60 Hz panel: one motion sample per frame is all the compositor can consume.
STEP_MS = 16


class Touch:
    def __init__(self):
        # A tracking id must be NEW for each contact. Reusing the same one
        # makes the stack treat the second touch-down as a continuation of the
        # first, already-released contact, and every gesture after the first
        # is silently dropped.
        self.tracking_id = 0
        self.fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        fcntl.ioctl(self.fd, UI_SET_KEYBIT, BTN_TOUCH)
        fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
        for a in (ABS_X, ABS_Y, ABS_MT_SLOT, ABS_MT_POSITION_X,
                  ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, a)

        absmax = [0] * ABS_CNT
        absmin = [0] * ABS_CNT
        for a in (ABS_X, ABS_MT_POSITION_X):
            absmax[a] = W - 1
        for a in (ABS_Y, ABS_MT_POSITION_Y):
            absmax[a] = H - 1
        absmax[ABS_MT_SLOT] = 9
        absmax[ABS_MT_TRACKING_ID] = 0xFFFF
        dev = struct.pack("@80sHHHHI" + "i" * (ABS_CNT * 4),
                          b"tk-virtual-touch", 0x03, 0x1209, 0x7a1e, 1, 0,
                          *(absmax + absmin + [0] * ABS_CNT + [0] * ABS_CNT))
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        # libinput only sees the device after udev tags it; without this the
        # first gesture is silently swallowed.
        time.sleep(1.0)

    def emit(self, *events):
        buf = b"".join(struct.pack(EV_FMT, 0, 0, t, c, v) for t, c, v in events)
        os.write(self.fd, buf + struct.pack(EV_FMT, 0, 0, EV_SYN, SYN_REPORT, 0))

    def down(self, x, y):
        self.tracking_id += 1
        self.emit((EV_ABS, ABS_MT_SLOT, 0),
                  (EV_ABS, ABS_MT_TRACKING_ID, self.tracking_id),
                  (EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y),
                  (EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y),
                  (EV_KEY, BTN_TOUCH, 1))

    def move(self, x, y):
        self.emit((EV_ABS, ABS_MT_SLOT, 0),
                  (EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y),
                  (EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y))

    def up(self):
        self.emit((EV_ABS, ABS_MT_SLOT, 0),
                  (EV_ABS, ABS_MT_TRACKING_ID, -1),
                  (EV_KEY, BTN_TOUCH, 0))

    def close(self):
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)


def drag(t, x1, y1, x2, y2, ms, lift_moving):
    steps = max(2, int(ms / STEP_MS))
    t.down(x1, y1)
    deadline = time.monotonic()
    for i in range(1, steps + 1):
        deadline += STEP_MS / 1000.0
        f = i / steps
        t.move(int(x1 + (x2 - x1) * f), int(y1 + (y2 - y1) * f))
        gap = deadline - time.monotonic()
        if gap > 0:
            time.sleep(gap)
    if not lift_moving:
        # settle so the compositor reads velocity 0 and does not fling
        time.sleep(0.08)
        t.move(x2, y2)
    t.up()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    t = Touch()
    try:
        if cmd in ("swipe", "fling"):
            x1, y1, x2, y2 = (int(v) for v in sys.argv[2:6])
            ms = int(sys.argv[6]) if len(sys.argv) > 6 else 300
            drag(t, x1, y1, x2, y2, ms, cmd == "fling")
            time.sleep(0.3)
        elif cmd == "tap":
            x, y = int(sys.argv[2]), int(sys.argv[3])
            t.down(x, y)
            time.sleep(0.06)
            t.up()
            time.sleep(0.3)
        elif cmd == "hold":
            time.sleep(float(sys.argv[2]))
        else:
            print(__doc__)
            return 1
    finally:
        t.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
