#!/usr/bin/env python3
# scope: generic
"""Print the CURRENT multitouch slot state of an evdev node, without waiting.

Run ON THE DEVICE, as root. Stdlib only.

tk-evtest.py shows the event *stream*; this shows the kernel's retained slot
state, which is what a compositor sees when it opens the device. The two differ
in exactly the case that matters here: if the controller never sends a LEAVE for
a finger, the stream looks idle while the state still holds a contact down. A
compositor reading that state believes a finger is pressed forever, and every
later tap lands as part of a gesture that never ends -- which looks to a user
like "the app froze" and like "touch stopped working", from one cause.

A healthy idle touchscreen reports tracking_id -1 in every slot.

  tk-mtstate.py [/dev/input/eventN]      default: auto-pick the ftm4 node
"""
import fcntl
import glob
import os
import struct
import sys

ABS_MT_SLOT = 0x2F
ABS_MT_TRACKING_ID = 0x39

EVIOCGNAME_LEN = 128


def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


_IOC_READ = 2

# EVIOCGABS(abs) = _IOR('E', 0x40 + abs, struct input_absinfo)
# struct input_absinfo { s32 value, minimum, maximum, fuzz, flat, resolution; }
ABSINFO_FMT = "<6i"


def eviocgname(fd):
    buf = bytearray(EVIOCGNAME_LEN)
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x06, EVIOCGNAME_LEN), buf)
    return buf.split(b"\x00")[0].decode("utf-8", "replace")


def eviocgabs(fd, code):
    buf = bytearray(struct.calcsize(ABSINFO_FMT))
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x40 + code,
                         struct.calcsize(ABSINFO_FMT)), buf)
    return struct.unpack(ABSINFO_FMT, bytes(buf))


def eviocgmtslots(fd, nslots):
    """EVIOCGMTSLOTS(len) -- s32 code followed by one s32 value per slot."""
    n = nslots + 1
    size = 4 * n
    buf = bytearray(struct.pack("<i", ABS_MT_TRACKING_ID) + b"\x00" * (size - 4))
    fcntl.ioctl(fd, _ioc(_IOC_READ, "E", 0x0A, size), buf)
    vals = struct.unpack("<%di" % n, bytes(buf))
    return vals[1:]


def find_ftm4():
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            if "ftm4" in eviocgname(fd).lower():
                return path
        except OSError:
            pass
        finally:
            os.close(fd)
    return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else find_ftm4()
    if not path:
        print("no ftm4 touchscreen found")
        return 1

    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        print("device: %s (%s)" % (path, eviocgname(fd)))
        slot_info = eviocgabs(fd, ABS_MT_SLOT)
        nslots = slot_info[2] + 1
        print("slots : %d" % nslots)

        ids = eviocgmtslots(fd, nslots)
        stuck = [i for i, v in enumerate(ids) if v != -1]
        for i, v in enumerate(ids):
            if v != -1:
                print("  slot %-2d tracking_id=%-6d  <== STILL DOWN" % (i, v))
        if stuck:
            print("\n%d slot(s) held down: %s" % (len(stuck), stuck))
            print("If nothing is touching the screen, these are STUCK "
                  "contacts and userspace believes a finger is pressed.")
        else:
            print("\nall slots idle (tracking_id -1) -- no stuck contacts")
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
