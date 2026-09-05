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
  TOP_APP      per-task uclamp_min on the threads of the apps in app.slice, so
               the scheduler places them on the big cluster. Android's top-app
               cpuset/uclamp, the one hint that moves PLACEMENT rather than
               frequency. OFF unless the unit passes a value: it measured null
               on scroll, see clamp_app_threads() for the numbers.

Hints are sysfs assignments given on the command line, so no device policy
lives in the tool; the unit file carries the numbers:

  powerhintd [--latency-us 44] \
      [--interaction-hold 3] --interaction PATH=VALUE [--interaction ...] \
      [--launch-hold 5]      --launch PATH=VALUE      [--launch ...] \
      [--top-app-uclamp-min 512] \
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
IN_IGNORED = 0x8000


# TOP_APP. sched_setattr(2) has no libc wrapper, so it goes through syscall(2);
# 274 is the arm64 native number. SCHED_FLAG_KEEP_ALL leaves policy, nice and
# priority untouched, and raising util_min alone needs no CAP_SYS_NICE -- only
# check_same_owner(), which holds because this daemon runs as root.
SYS_sched_setattr = 274
SCHED_FLAG_KEEP_ALL = 0x08 | 0x10          # KEEP_POLICY | KEEP_PARAMS
SCHED_FLAG_UTIL_CLAMP_MIN = 0x20


class sched_attr(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("sched_policy", ctypes.c_uint32),
                ("sched_flags", ctypes.c_uint64), ("sched_nice", ctypes.c_int32),
                ("sched_priority", ctypes.c_uint32), ("sched_runtime", ctypes.c_uint64),
                ("sched_deadline", ctypes.c_uint64), ("sched_period", ctypes.c_uint64),
                ("sched_util_min", ctypes.c_uint32), ("sched_util_max", ctypes.c_uint32)]


def clamp_app_threads(libc, app_slice, util_min):
    """uclamp_min every thread of every app cgroup. Returns the count set.

    WHY THIS HINT EXISTS: it is the one Android lever this daemon was missing.
    INTERACTION and LAUNCH both move frequency; top-app moves PLACEMENT.

    WHAT IT IS WORTH: unknown, and measured null so far. On taimen 2026-09-05,
    tk-scrollarm.sh, jank frames >33 ms in a 10 s drag of a 79000px document:

        eleven control arms, five sweeps   32 36 37 38 38 39 41 42 43 43 44 46
                                           -> mean 40.1, sd 4.1
        TOP_APP 512, browser only (4 arms) 39 34 36 42   vs off 37 36 38 43
        uclamp 512 phoc+phosh only (2)     40 38
        uclamp 512 browser only (2)        40 39
        both clusters pinned MAX freq (2)  40 46
        uclamp 1024 on everything (2)      47 48

    Nothing clears the noise. That sd puts a single arm's 95% interval at +/-8
    jank and a two-arm mean at +/-5.7, so two arms a side resolve only a ~30%
    change. An earlier two-arm reading of this same hint looked like a clean
    -20% and did not survive four arms and an end-to-end test through this
    daemon -- the same error the commit before this one warns about. So the
    unit file ships NO --top-app-uclamp-min and the hint is inert by default.

    What IS verified is that the mechanism works: a browser started after the
    daemon, with no touch input at all, comes up with uclamp.min set on its main
    thread and its ThreadedCompositor.

    1024 is the one value with a hint of a real effect, and it is negative:
    clamping all 24 threads of the process, JIT and raster workers included, to
    full utilisation piles them onto the four big cores to contend.

    Deliberately STATELESS -- it re-clamps threads it has already clamped rather
    than remembering them. A browser spawns its web process seconds after its
    scope appears, and a set of "already done" tids would either miss those or
    go stale as tids get reused; sched_setattr on a thread that already holds
    the value costs a few microseconds and cannot be wrong.

    ponytail: clamps every app in app.slice, not just the focused one. Real
    top-app tracking needs a focus signal this daemon does not have, and
    uclamp_min only bites while a thread is actually runnable, so a backgrounded
    app costs nothing. Narrow it if a background app ever starts spinning.
    """
    attr = sched_attr()
    attr.size = ctypes.sizeof(sched_attr)
    attr.sched_flags = SCHED_FLAG_KEEP_ALL | SCHED_FLAG_UTIL_CLAMP_MIN
    attr.sched_util_min = util_min
    done = 0
    for root, _dirs, files in os.walk(app_slice):
        if "cgroup.procs" not in files:
            continue
        try:
            with open(os.path.join(root, "cgroup.procs")) as f:
                pids = f.read().split()
        except OSError:
            continue
        for pid in pids:
            try:
                tids = os.listdir(f"/proc/{pid}/task")
            except OSError:
                continue                      # exited between the two reads
            for tid in tids:
                if libc.syscall(SYS_sched_setattr, ctypes.c_int(int(tid)),
                                ctypes.byref(attr), ctypes.c_uint(0)) == 0:
                    done += 1
    return done


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
    ap.add_argument("--top-app-uclamp-min", type=int, default=0, metavar="N",
                    help="0-1024 uclamp_min for app.slice threads; 0 disables. "
                         "Needs --app-slice. Peaks in the middle -- see "
                         "clamp_app_threads()")
    args = ap.parse_args()

    if args.top_app_uclamp_min:
        if not args.app_slice:
            sys.exit("--top-app-uclamp-min needs --app-slice")
        if not 0 < args.top_app_uclamp_min <= 1024:
            sys.exit("--top-app-uclamp-min must be 1-1024")

    by_hint = {}
    if args.interaction:
        by_hint["INTERACTION"] = parse_specs(args.interaction)
    if args.launch:
        if not args.app_slice:
            sys.exit("--launch needs --app-slice")
        by_hint["LAUNCH"] = parse_specs(args.launch)
    if not by_hint and not args.top_app_uclamp_min:
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
    names = list(by_hint) + (["TOP_APP"] if args.top_app_uclamp_min else [])
    print(f"watching {node}; hints: {', '.join(names)}", flush=True)

    # TOP_APP rescans on touch, but no faster than this: a drag delivers events
    # continuously and walking every app cgroup per event would burn more CPU
    # than the hint saves.
    TOP_APP_RESCAN = 2.0
    top_app_at = 0.0

    def clamp_top_app():
        nonlocal top_app_at
        now = time.monotonic()
        if (not args.top_app_uclamp_min or not args.app_slice
                or now - top_app_at < TOP_APP_RESCAN):
            return
        top_app_at = now
        clamp_app_threads(libc, args.app_slice, args.top_app_uclamp_min)

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
                clamp_top_app()     # catches threads spawned since the launch
            if ino_fd in r:
                buf = os.read(ino_fd, 4096)
                off = 0
                while off < len(buf):
                    _, mask, _, ln = struct.unpack_from("iIII", buf, off)
                    name = buf[off + 16:off + 16 + ln].rstrip(b"\0").decode()
                    off += 16 + ln
                    if mask & IN_IGNORED:
                        # watched cgroup destroyed (session restart);
                        # re-arm lazily on the next touch
                        watched = False
                        continue
                    if name.startswith("app-"):
                        if "LAUNCH" in by_hint:
                            hints.fire("LAUNCH", args.launch_hold)
                        top_app_at = 0.0    # a new app: clamp it now, not in 2 s
                        clamp_top_app()
            hints.reap()
            # Every wakeup, throttled. A launch creates the scope seconds before
            # the app's real worker processes exist -- a browser's web process
            # is not there when its scope appears -- so clamping only on the
            # inotify event misses exactly the threads that matter. Any later
            # wakeup (a hint expiring, the next touch) sweeps them up.
            clamp_top_app()
    finally:
        hints.disengage()


if __name__ == "__main__":
    main()
