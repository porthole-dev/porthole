#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: soc:qcom
# needs: - (host only, no device)
# env: -
# exits: 0 ok
"""Enable an IIO event and print events as they arrive. Runs ON the device.

The event-only SMGR sensors -- step detector, activity, inactivity and the
three FTM4 wake gestures -- have no readable value at all: they produce a
report exactly when the event fires and nothing in between. So there is no
sysfs attribute to watch, and an event that never fires is indistinguishable
from one that is wired up wrong. This reads the event chardev, which is the
only way to tell those apart.

Enabling the event is also what starts the subscription, so this has to write
the _en attribute before anything can arrive.

    ph-iio-events.py qcom-smgr-double-tap [seconds]
    ph-iio-events.py --list

Trigger the gesture, walk, or hold still, depending on which one you picked.
"""
import ctypes
import fcntl
import os
import select
import struct
import sys
import time

IIO = '/sys/bus/iio/devices'
# _IOR('i', 0x90, int) -- struct iio_event_data { __u64 id; __s64 timestamp; }
IIO_GET_EVENT_FD_IOCTL = 0x80046990


def devices():
    out = []
    for d in sorted(os.listdir(IIO)):
        p = f'{IIO}/{d}'
        try:
            name = open(f'{p}/name').read().strip()
            evs = sorted(os.listdir(f'{p}/events'))
        except OSError:
            continue
        if evs:
            out.append((name, p, d, evs))
    return out


def main():
    if '--list' in sys.argv or len(sys.argv) < 2:
        for name, _, dev, evs in devices():
            print(f'  {name:<26} {dev:<14} {" ".join(evs)}')
        return 0

    want = sys.argv[1]
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    match = [d for d in devices() if d[0] == want]
    if not match:
        sys.exit(f'no IIO device named {want} with events -- try --list')
    name, path, dev, evs = match[0]
    en = [e for e in evs if e.endswith('_en')]
    if not en:
        sys.exit(f'{name} has no _en attribute')

    for e in en:
        with open(f'{path}/events/{e}', 'w') as f:
            f.write('1')
    print(f'{name}: enabled {", ".join(en)}; listening {secs}s')

    fd = os.open(f'/dev/{dev}', os.O_RDONLY)
    try:
        efd = ctypes.c_int()
        fcntl.ioctl(fd, IIO_GET_EVENT_FD_IOCTL, efd)
        efd = efd.value
        end = time.time() + secs
        n = 0
        while time.time() < end:
            r, _, _ = select.select([efd], [], [], min(1.0, end - time.time()))
            if not r:
                continue
            buf = os.read(efd, 16)
            if len(buf) < 16:
                continue
            ev_id, ts = struct.unpack('<Qq', buf)
            n += 1
            print(f'  event {n:3d}  id=0x{ev_id:016x}  ts={ts}')
            sys.stdout.flush()
        os.close(efd)
    finally:
        os.close(fd)
        for e in en:
            try:
                with open(f'{path}/events/{e}', 'w') as f:
                    f.write('0')
            except OSError:
                pass

    print(f'{n} events in {secs}s')
    return 0 if n else 1


if __name__ == '__main__':
    sys.exit(main())
