#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Dump multitouch events from an evdev node. Run ON THE DEVICE, as root.

There is no evtest in the rootfs and no python-evdev, so this decodes
struct input_event straight off /dev/input/eventN with the stdlib.

  tk-evtest.py                 auto-pick the ftm4 touchscreen, run until Ctrl-C
  tk-evtest.py 15              run for 15 s and print a per-slot summary
  tk-evtest.py 15 /dev/input/event3

The summary is what makes this useful for bring-up: it reports how many
distinct slots were seen, so a two-finger test proves the touch-id decoding
(the id-1 events that a whole-first-byte status match would swallow).
"""
import os
import struct
import sys
import time

# struct input_event on 64-bit: two __kernel_ulong_t, then u16 u16 s32.
EVENT_FMT = "QQHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
BTN_TOUCH = 0x14a
ABS_MT_SLOT = 0x2f
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_TOUCH_MINOR = 0x31
ABS_MT_ORIENTATION = 0x34
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
ABS_MT_PRESSURE = 0x3a

ABS_NAMES = {
    ABS_MT_SLOT: "SLOT", ABS_MT_TOUCH_MAJOR: "MAJOR",
    ABS_MT_TOUCH_MINOR: "MINOR", ABS_MT_ORIENTATION: "ORIENT",
    ABS_MT_POSITION_X: "X", ABS_MT_POSITION_Y: "Y",
    ABS_MT_TRACKING_ID: "TRACKING_ID", ABS_MT_PRESSURE: "PRESSURE",
}


def find_touchscreen(name_hint="ftm4"):
    """Locate the event node by walking /proc/bus/input/devices."""
    handlers, matched = None, False
    with open("/proc/bus/input/devices") as f:
        for line in f:
            line = line.strip()
            if line.startswith("N: Name="):
                matched = name_hint.lower() in line.lower()
            elif line.startswith("H: Handlers=") and matched:
                handlers = line.split("=", 1)[1].split()
                break
    if not handlers:
        return None
    for h in handlers:
        if h.startswith("event"):
            return "/dev/input/" + h
    return None


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else None
    path = sys.argv[2] if len(sys.argv) > 2 else find_touchscreen()
    if not path:
        sys.exit("no ftm4 input device -- is the driver bound? "
                 "check /proc/bus/input/devices")
    print(f"reading {path}"
          + (f" for {seconds:g}s" if seconds else " (Ctrl-C to stop)"))

    slot = 0
    down = {}                      # slot -> tracking id
    seen_slots = set()
    contacts = 0
    frame = {}                     # slot -> {field: value}, flushed on EV_SYN
    end = time.time() + seconds if seconds else None

    with open(path, "rb", buffering=0) as f:
        os.set_blocking(f.fileno(), False)
        while end is None or time.time() < end:
            data = f.read(EVENT_SIZE)
            if not data:
                time.sleep(0.005)
                continue
            _, _, etype, code, value = struct.unpack(EVENT_FMT, data)

            if etype == EV_ABS:
                if code == ABS_MT_SLOT:
                    slot = value
                    seen_slots.add(slot)
                elif code == ABS_MT_TRACKING_ID:
                    seen_slots.add(slot)
                    if value == -1:
                        down.pop(slot, None)
                        print(f"  UP     slot={slot}")
                    else:
                        down[slot] = value
                        contacts += 1
                        print(f"  DOWN   slot={slot} tracking_id={value}")
                elif code in ABS_NAMES:
                    frame.setdefault(slot, {})[ABS_NAMES[code]] = value
                    seen_slots.add(slot)
            elif etype == EV_KEY and code == BTN_TOUCH:
                print(f"  BTN_TOUCH={value}")
            elif etype == EV_SYN:
                # One frame can carry several slots; keep them apart.
                for s in sorted(frame):
                    fields = " ".join(f"{k}={v}" for k, v in frame[s].items())
                    print(f"  slot={s} {fields}")
                frame.clear()

    print(f"\n--- summary ---\n"
          f"contacts (DOWN events): {contacts}\n"
          f"distinct slots seen   : {sorted(seen_slots)}\n"
          f"still down at exit    : {sorted(down)}")


if __name__ == "__main__":
    main()
