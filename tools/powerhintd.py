#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (installed as a systemd service; see powerhintd.service)
# env: -
# exits: 0 clean stop · 1 no touchscreen, or a bad boost spec
"""powerhintd -- Android's power hints for a mainline phone.

Android's PowerHAL (powerhint.json, libperfmgr) is the userspace half of the
vendor's smoothness story: the kernel provides mechanisms, and a daemon applies
short-lived policy on events the kernel cannot see. This is that daemon for a
systemd/phosh device, with the two hints that measured on taimen 2026-09-01
(brain/findings/android-interaction-boost-is-the-remaining-perf-delta.md):

  INTERACTION  while the touchscreen emits events, plus a hold-off: floor the
               CPU clusters and hold /dev/cpu_dma_latency low. Neither half
               works alone -- floors-only and latency-only both still jank.
  LAUNCH       when an app-*.{scope,slice} cgroup appears in the session's
               app.slice (how systemd represents "an app was launched"): pin
               the clusters high for the launch window.

Hints are sysfs assignments given on the command line, so no device policy
lives in the tool; the unit file carries the numbers:

  powerhintd [--latency-us 44] \
      [--interaction-hold 3] --interaction PATH=VALUE [--interaction ...] \
      [--launch-hold 5]      --launch PATH=VALUE      [--launch ...] \
      [--app-slice /sys/fs/cgroup/.../app.slice]

While any hint is active the daemon owns the target files: it saves their
pre-hint content on the first engagement and restores it when the last hint
releases, so an external change made while idle (a governor switch, a thermal
cap) is respected. Overlapping hints write, per path, the numerically largest
value any active hint requests. The touchscreen is found by ABS_MT_POSITION_X
capability. Idle cost is a blocked select(); no timers tick while nothing
happens. The app.slice watch is (re)established lazily on touch activity, so
a daemon started before the user session exists needs no polling and no
ordering dependency to pick it up.
"""
import argparse
import ctypes
import os
import select
import signal
import struct
import sys
import time

DEVICES = "/proc/bus/input/devices"
ABS_MT_POSITION_X = 0x35
EVENT_SIZE = struct.calcsize("llHHi")
IN_CREATE = 0x100


def find_touchscreen():
    """The event node whose ABS capability mask has ABS_MT_POSITION_X."""
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


def parse_specs(specs):
    out = []
    for s in specs or []:
        path, _, value = s.partition("=")
        if not value or not os.path.exists(path):
            sys.exit(f"bad or missing hint target: {s}")
        out.append((path, value))
    return out


class Hints:
    """The active-hint set and the sysfs writes it implies."""

    def __init__(self, by_hint, latency_us):
        self.by_hint = by_hint          # hint name -> [(path, value)]
        self.latency_us = latency_us
        self.expiry = {}                # hint name -> monotonic deadline
        self.baseline = None            # [(path, saved value)] while engaged
        self.qos_fd = None
        self.written = {}               # path -> value last written

    def paths(self):
        return {p for spec in self.by_hint.values() for p, _ in spec}

    def fire(self, name, hold):
        self.expiry[name] = time.monotonic() + hold
        self.apply()

    def reap(self):
        now = time.monotonic()
        for name in [n for n, t in self.expiry.items() if t <= now]:
            del self.expiry[name]
        self.apply()

    def next_deadline(self):
        return min(self.expiry.values()) if self.expiry else None

    def apply(self):
        if not self.expiry:
            self.disengage()
            return
        if self.baseline is None:
            self.baseline = []
            for path in sorted(self.paths()):
                with open(path) as f:
                    self.baseline.append((path, f.read().strip()))
        if self.latency_us and self.qos_fd is None:
            self.qos_fd = os.open("/dev/cpu_dma_latency", os.O_WRONLY)
            os.write(self.qos_fd, struct.pack("i", self.latency_us))
        want = {}
        for name in self.expiry:
            for path, value in self.by_hint[name]:
                if path not in want or int(value) > int(want[path]):
                    want[path] = value
        for path, value in want.items():
            if self.written.get(path) != value:
                with open(path, "w") as f:
                    f.write(value)
                self.written[path] = value

    def disengage(self):
        for path, value in self.baseline or []:
            try:
                with open(path, "w") as f:
                    f.write(value)
            except OSError:
                pass
        self.baseline = None
        self.written = {}
        if self.qos_fd is not None:
            os.close(self.qos_fd)   # closing drops the PM QoS request
            self.qos_fd = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency-us", type=int, default=44,
                    help="PM QoS cpu_dma_latency held while any hint is active; 0 disables")
    ap.add_argument("--interaction", action="append", metavar="PATH=VALUE")
    ap.add_argument("--interaction-hold", type=float, default=3.0)
    ap.add_argument("--launch", action="append", metavar="PATH=VALUE")
    ap.add_argument("--launch-hold", type=float, default=5.0)
    ap.add_argument("--app-slice", metavar="CGROUP_DIR",
                    help="session app.slice cgroup directory to watch for launches")
    args = ap.parse_args()

    by_hint = {}
    if args.interaction:
        by_hint["INTERACTION"] = parse_specs(args.interaction)
    if args.launch:
        if not args.app_slice:
            sys.exit("--launch needs --app-slice")
        by_hint["LAUNCH"] = parse_specs(args.launch)
    if not by_hint:
        sys.exit("no hints configured")
    hints = Hints(by_hint, args.latency_us)

    node = find_touchscreen()
    if node is None:
        sys.exit("no device with ABS_MT_POSITION_X in " + DEVICES)
    touch_fd = os.open(node, os.O_RDONLY)

    libc = ctypes.CDLL(None, use_errno=True)
    ino_fd = libc.inotify_init1(0)
    watched = False

    def watch_app_slice():
        nonlocal watched
        if watched or not args.app_slice or not os.path.isdir(args.app_slice):
            return
        if libc.inotify_add_watch(ino_fd, args.app_slice.encode(), IN_CREATE) >= 0:
            watched = True
            print(f"watching {args.app_slice} for launches", flush=True)

    watch_app_slice()
    print(f"watching {node}; hints: {', '.join(by_hint)}", flush=True)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            deadline = hints.next_deadline()
            timeout = max(0.0, deadline - time.monotonic()) if deadline else None
            fds = [touch_fd] + ([ino_fd] if watched else [])
            r, _, _ = select.select(fds, [], [], timeout)
            if touch_fd in r:
                os.read(touch_fd, EVENT_SIZE * 64)   # drain; content irrelevant
                if "INTERACTION" in by_hint:
                    hints.fire("INTERACTION", args.interaction_hold)
                watch_app_slice()   # lazy: a touch means the session exists
            if ino_fd in r:
                buf = os.read(ino_fd, 4096)
                off = 0
                while off < len(buf):
                    _, _, _, ln = struct.unpack_from("iIII", buf, off)
                    name = buf[off + 16:off + 16 + ln].rstrip(b"\0").decode()
                    off += 16 + ln
                    if name.startswith("app-"):
                        hints.fire("LAUNCH", args.launch_hold)
            hints.reap()
    finally:
        hints.disengage()


if __name__ == "__main__":
    main()
