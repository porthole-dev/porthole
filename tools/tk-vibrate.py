#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
"""Play a rumble effect through the force-feedback API. Run ON THE DEVICE.

This is the path userspace haptics actually use -- feedbackd uploads an
FF_RUMBLE effect to the evdev node and plays it -- so exercising it here tests
the same thing phosh will, rather than some driver-private sysfs knob.

Stdlib only.

  tk-vibrate.py [MAGNITUDE_PCT] [MS] [REPEATS]      default 100 300 3
  tk-vibrate.py --list                              show ff-capable devices
"""
import fcntl
import glob
import os
import struct
import sys
import time

EV_FF = 0x15
EV_SYN, SYN_REPORT = 0x00, 0x00
FF_RUMBLE = 0x50

# struct ff_effect is 48 bytes on 64-bit: the union is aligned to 8 by the
# pointer inside ff_periodic_effect, so the rumble members land at offset 16.
FF_EFFECT_SIZE = 48
EVIOCSFF = (1 << 30) | (FF_EFFECT_SIZE << 16) | (ord('E') << 8) | 0x80
EVIOCRMFF = (1 << 30) | (4 << 16) | (ord('E') << 8) | 0x81


def ff_devices():
    out = []
    for path in sorted(glob.glob("/dev/input/event*")):
        name_file = "/sys/class/input/%s/device/name" % os.path.basename(path)
        try:
            with open(name_file) as f:
                name = f.read().strip()
        except OSError:
            continue
        caps = "/sys/class/input/%s/device/capabilities/ff" % os.path.basename(path)
        try:
            # A multi-word bitmap, e.g. "107030000 0" -- not a single integer.
            with open(caps) as f:
                has_ff = any(int(w, 16) for w in f.read().split())
        except (OSError, ValueError):
            has_ff = False
        if has_ff:
            out.append((path, name))
    return out


def rumble_effect(strong, weak, ms):
    """A whole struct ff_effect, id -1 so the kernel allocates one."""
    head = struct.pack("<HhHHHHH", FF_RUMBLE, -1, 0, 0, 0, ms, 0)
    return (head + b"\x00" * (16 - len(head))
            + struct.pack("<HH", strong, weak) + b"\x00" * (FF_EFFECT_SIZE - 20))


def main():
    if "--list" in sys.argv:
        for path, name in ff_devices():
            print("%s\t%s" % (path, name))
        return 0

    pct = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    ms = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    repeats = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    devs = ff_devices()
    if not devs:
        print("no force-feedback device -- is the vibrator driver bound?")
        return 1
    path, name = devs[0]
    mag = max(0, min(0xFFFF, pct * 0xFFFF // 100))
    print("playing on %s (%s): %d%% for %d ms x%d" % (path, name, pct, ms, repeats))

    fd = os.open(path, os.O_RDWR)
    try:
        buf = bytearray(rumble_effect(mag, mag, ms))
        fcntl.ioctl(fd, EVIOCSFF, buf)
        effect_id = struct.unpack_from("<h", buf, 2)[0]
        print("uploaded effect id %d" % effect_id)
        for _ in range(repeats):
            os.write(fd, struct.pack("@llHHi", 0, 0, EV_FF, effect_id, 1))
            time.sleep(ms / 1000.0 + 0.25)
        fcntl.ioctl(fd, EVIOCRMFF, effect_id)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
