#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (installed as a systemd service; see tk-touchboost.service)
# env: -
# exits: 0 clean stop · 1 no touchscreen, or a bad boost spec
"""Hold Android's INTERACTION boost while the screen is being touched.

Android's PowerHAL fires an INTERACTION hint on every touch: it floors both
CPU clusters near mid-frequency AND holds /dev/cpu_dma_latency low so cores
stop entering deep idle between frames. Measured on taimen 2026-09-01, the
pair is what separates a janky fling from a clean one -- and NEITHER HALF
WORKS ALONE (floors-only still dropped frames, latency-only too; see
brain/findings/android-interaction-boost-is-the-remaining-perf-delta.md).
This is that hint for a mainline phone: watch the touchscreen, and while a
finger is down (plus a hold-off), apply both.

Boost specs are sysfs assignments, so the tool has no device policy in it:

  tk-touchboost.py [--hold S] [--latency-us N] PATH=VALUE [PATH=VALUE...]

  tk-touchboost.py \
      /sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq=1134000 \
      /sys/devices/system/cpu/cpufreq/policy4/scaling_min_freq=1132800

Each PATH's pre-boost content is read when the boost engages and written back
when it releases, so an external change while idle (a governor switch, a
thermal cap) is respected rather than clobbered. --latency-us (default 44,
Android's value; 0 disables) is held open as a PM QoS request for the same
window. The touchscreen is found by ABS_MT_POSITION_X capability; sleeps in
select() between events, so an idle phone pays nothing.
"""
import argparse
import os
import select
import signal
import struct
import sys

DEVICES = "/proc/bus/input/devices"
ABS_MT_POSITION_X = 0x35
EVENT_SIZE = struct.calcsize("llHHi")


def find_touchscreen():
    """The event node whose ABS capability mask has ABS_MT_POSITION_X."""
    handler, absmask = None, 0
    with open(DEVICES) as f:
        for block in f.read().split("\n\n"):
            handler, absmask = None, 0
            for line in block.splitlines():
                if line.startswith("H: Handlers="):
                    for tok in line.split("=", 1)[1].split():
                        if tok.startswith("event"):
                            handler = tok
                elif line.startswith("B: ABS="):
                    for word in line.split("=", 1)[1].split():
                        absmask = (absmask << 64) | int(word, 16)
            if handler and absmask >> ABS_MT_POSITION_X & 1:
                return "/dev/input/" + handler
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=3.0,
                    help="seconds after the last event before releasing")
    ap.add_argument("--latency-us", type=int, default=44,
                    help="PM QoS cpu_dma_latency to hold; 0 disables")
    ap.add_argument("spec", nargs="+", metavar="PATH=VALUE")
    args = ap.parse_args()

    boosts = []
    for s in args.spec:
        path, _, value = s.partition("=")
        if not value or not os.path.exists(path):
            sys.exit(f"bad or missing boost target: {s}")
        boosts.append((path, value))

    node = find_touchscreen()
    if node is None:
        sys.exit("no device with ABS_MT_POSITION_X in " + DEVICES)
    fd = os.open(node, os.O_RDONLY)
    print(f"watching {node}; hold {args.hold}s; {len(boosts)} sysfs target(s)",
          flush=True)

    saved, qos_fd = None, None

    def engage():
        nonlocal saved, qos_fd
        saved = []
        for path, value in boosts:
            with open(path) as f:
                saved.append((path, f.read().strip()))
            with open(path, "w") as f:
                f.write(value)
        if args.latency_us:
            qos_fd = os.open("/dev/cpu_dma_latency", os.O_WRONLY)
            os.write(qos_fd, struct.pack("i", args.latency_us))

    def release():
        nonlocal saved, qos_fd
        for path, value in saved or []:
            try:
                with open(path, "w") as f:
                    f.write(value)
            except OSError:
                pass
        saved = None
        if qos_fd is not None:
            os.close(qos_fd)  # closing drops the PM QoS request
            qos_fd = None

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            timeout = args.hold if saved is not None else None
            r, _, _ = select.select([fd], [], [], timeout)
            if not r:
                release()
                continue
            os.read(fd, EVENT_SIZE * 64)  # drain; content irrelevant
            if saved is None:
                engage()
    finally:
        release()


if __name__ == "__main__":
    main()
