#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
"""Measure how smooth the phosh session actually is. Run ON THE DEVICE as root.

Average FPS is the wrong number. A session that renders 58 frames in a second
but stalls 100 ms in the middle of a scroll reads as broken, while a steady 45
reads as fine. So this samples the DPU's own vsync counter fast enough to
recover *individual frame intervals* and reports their distribution: p50 is
what the session normally does, p95/max is what the user actually notices.

Needs ph-touch.py and ph-ui.py beside it, and ON THE DEVICE `grim` plus one
of `lswt` or `wlrctl` -- ph-ui.py shells out to them to identify the focused
surface. Neither is in the pmOS image by default: `apk add grim wlrctl`.
lswt is not packaged by Alpine at all, so it does not survive a rootfs
reflash; ph-ui.py falls back to wlrctl, which is packaged.

  ph-gesture-bench.py NAME [REPEATS]
  ph-gesture-bench.py drag X1 Y1 X2 Y2 MS [REPEATS] [--fling] [--pause MS]
  ph-gesture-bench.py watch SECONDS          measure without touching anything
  ... [--client LOG]  add the APP's own frame rate beside the DPU's

The DPU counter is phoc's output rate, not the app's: phoc repaints every
vsync while any client animates, so a browser committing every fourth vsync
still reads 60 fps / 0 jank. Launch the app with `WAYLAND_DEBUG=1 app 2>LOG`
and pass `--client LOG`: the run is bracketed against the log and the app's
own wl_surface.commit intervals -- and its presentation-feedback vsync
deltas, which are exact -- are reported next to the compositor's.
  ph-gesture-bench.py latency X Y [REPEATS] [IDLE_S]
        Idle until the GPU and DPU have powered down, then touch and time the
        first frame out. This is the number a user calls "laggy": average FPS
        says nothing about how long the screen sits still after a finger lands.

NAME is one of the gestures in SCENES below.
"""
import collections
import os
import pathlib
import re
import statistics
import sys
import threading
import glob
import time



def _sibling(stem):
    """Load ph-touch.py / ph-ui.py by PATH.

    `from tk_touch import ...` cannot work: the file is ph-touch.py and a
    hyphen is not a module name, so this tool has never once imported. Nothing
    in the tree renames the file either, on the host or when pushing to /tmp.
    Importing by path is the fix that does not depend on how it was deployed.
    """
    import importlib.util
    here = pathlib.Path(__file__).resolve().parent
    for cand in (here / f"{stem}.py", pathlib.Path("/tmp") / f"{stem}.py"):
        if not cand.is_file():
            continue
        spec = importlib.util.spec_from_file_location(stem.replace("-", "_"), cand)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    raise SystemExit(f"{stem}.py must sit beside this tool, or in /tmp")


_touch = _sibling("ph-touch")
Touch, drag = _touch.Touch, _touch.drag
_ui = _sibling("ph-ui")
screen_hash, settle, toplevels = _ui.screen_hash, _ui.settle, _ui.toplevels
focus = _ui.focus
unblank = _ui.unblank

# 6.0 named it encoder31, 6.18 names it encoder-0. Glob, don't guess.
ENCODER = next(iter(glob.glob("/sys/kernel/debug/dri/0/encoder*/status")),
               "/sys/kernel/debug/dri/0/encoder-0/status")
POLL_S = 0.002
FRAME_MS = 1000.0 / 60.0

# Each scene is (description, fn(touch)). Coordinates are 1440x2880 panel px.
SCENES = {
    "grid-fling": ("app grid flung up and down",
                   lambda t: (drag(t, 720, 2300, 720, 900, 250, True),
                              time.sleep(0.8),
                              drag(t, 720, 900, 720, 2300, 250, True),
                              time.sleep(0.8))),
    "grid-drag": ("app grid dragged slowly, finger down the whole time",
                  lambda t: (drag(t, 720, 2300, 720, 800, 900, False),
                             time.sleep(0.3),
                             drag(t, 720, 800, 720, 2300, 900, False),
                             time.sleep(0.3))),
    # Quick settings only closes from the chevron handle at the very bottom;
    # dragging up from the middle of the open panel does nothing, which leaves
    # it stuck open and every later repeat measuring an empty screen.
    "top-panel": ("top panel pulled down and pushed back up",
                  lambda t: (drag(t, 720, 20, 720, 1600, 400, False),
                             time.sleep(0.6),
                             drag(t, 720, 2820, 720, 1200, 400, False),
                             time.sleep(0.6))),
    "overview": ("home gesture: swipe up from the bottom bar",
                 lambda t: (drag(t, 720, 2860, 720, 1800, 300, True),
                            time.sleep(1.2))),
}


# WAYLAND_DEBUG stamps: "H:M:S.mmm" since libwayland 1.22, plain milliseconds
# before it. Both are in the wild (the phone prints the first), so accept either.
_WL_COMMIT = re.compile(r"\[([\d:.]+)\].* -> (wl_surface#\d+)\.commit\(\)")
_WL_PRESENTED = re.compile(r"wp_presentation_feedback#\d+\.presented\(([^)]*)\)")


def _wl_seconds(stamp):
    if ":" in stamp:
        h, m, sec = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    return float(stamp) / 1000.0


def client_frames(lines):
    """(surface, [interval ms], [vsyncs between presents]) from a WAYLAND_DEBUG log.

    The busiest wl_surface is the app's own toplevel: a client also commits to
    cursors, subsurfaces and popups, and counting those together would make a
    stalled window look busy. Pure on purpose -- this is the decision the whole
    mode rests on, and it can be wrong in a test instead of on a device.
    """
    commits, presented = {}, []
    for line in lines:
        m = _WL_COMMIT.search(line)
        if m:
            commits.setdefault(m.group(2), []).append(_wl_seconds(m.group(1)))
        m = _WL_PRESENTED.search(line)
        if m:
            a = [x.strip() for x in m.group(1).split(",")]
            # tv_sec_hi, tv_sec_lo, tv_nsec, refresh, seq_hi, seq_lo, flags
            if len(a) >= 7 and a[5].isdigit():
                presented.append(int(a[5]))
    surface, stamps = (max(commits.items(), key=lambda kv: len(kv[1]))
                       if commits else (None, []))
    gaps = sorted((b - a) * 1000.0 for a, b in zip(stamps, stamps[1:]))
    return surface, gaps, [b - a for a, b in zip(presented, presented[1:]) if b > a]


def report_client(surface, gaps, seq):
    """The app's column. Same 250 ms idle cut as the DPU column above."""
    gaps = [g for g in gaps if g < 250.0]
    if len(gaps) < 5:
        print("  client: %d intervals on %s -- the app committed almost nothing"
              % (len(gaps), surface or "no wl_surface in the log"))
        return
    p = lambda q: gaps[min(len(gaps) - 1, int(len(gaps) * q))]
    print("  client %s: commits=%d  p50=%.1f p90=%.1f p95=%.1f max=%.1f  "
          "jank>33ms=%d" % (surface, len(gaps) + 1, p(.50), p(.90), p(.95),
                            gaps[-1], sum(1 for g in gaps if g > 33.0)))
    if seq:
        print("  client presented every N vsyncs: %s (1 = every frame)"
              % dict(collections.Counter(seq).most_common(4)))


def _log_slice(path, offset):
    with open(path, errors="replace") as f:
        f.seek(offset)
        return f.read().splitlines()


def vsync():
    with open(ENCODER) as f:
        s = f.read()
    i = s.index("vsync:")
    return int(s[i + 6:s.index("underrun:", i)])


GPU_FREQ = "/sys/class/devfreq/5000000.gpu/cur_freq"


def gpu_mhz():
    try:
        with open(GPU_FREQ) as f:
            return int(f.read()) // 1000000
    except (OSError, ValueError):
        return -1


def pid_of(name):
    import os
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open("/proc/%s/comm" % pid) as f:
                    if f.read().strip() == name:
                        return int(pid)
            except OSError:
                pass
    return None


class Cpu:
    """utime+stime in 10 ms ticks, so a stall can be blamed on whoever burned
    the CPU during it -- or on nobody, which means it was a wait."""

    def __init__(self, names):
        self.pids = [(n, pid_of(n)) for n in names]
        self.pids = [(n, p) for n, p in self.pids if p]

    def read(self):
        # Whole-system busy first: without it a gap where the shell was idle
        # reads as "nobody was on CPU", when in practice the *app* was -- and
        # that is the difference between blaming the compositor and blaming
        # the client.
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:9]]
            out = [(sum(v) - v[3] - v[4])]
        except (OSError, ValueError):
            out = [0]
        for name, pid in self.pids:
            try:
                with open("/proc/%d/stat" % pid) as f:
                    p = f.read().rsplit(") ", 1)[-1].split()
                out.append(int(p[11]) + int(p[12]))
            except (OSError, IndexError, ValueError):
                out.append(0)
        return out

    @property
    def labels(self):
        return ["all-cpu"] + [n for n, _ in self.pids]


class Sampler(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.stamps = []
        self.mhz = []
        self.cpu = []
        self.proc = Cpu(["phoc", "phosh"])
        self.stop = False

    def run(self):
        last = vsync()
        while not self.stop:
            v = vsync()
            if v != last:
                # one entry per frame, so a burst of N is not collapsed into one
                now = time.monotonic()
                self.stamps.extend([now] * (v - last))
                # GPU clock as the frame landed: tells a stall spent waiting on
                # a low OPP apart from one spent waiting on the CPU.
                self.mhz.extend([gpu_mhz()] * (v - last))
                self.cpu.extend([self.proc.read()] * (v - last))
                last = v
            time.sleep(POLL_S)


def report(name, desc, stamps, wall, mhz=None, cpu=None, labels=()):
    gaps = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
    # An idle gap between the two halves of a gesture is not a dropped frame.
    gaps = [g for g in gaps if g < 250.0]
    if len(gaps) < 5:
        print("%-12s FEW FRAMES: %d in %.1fs -- nothing animated" %
              (name, len(stamps), wall))
        return
    gaps.sort()
    p = lambda q: gaps[min(len(gaps) - 1, int(len(gaps) * q))]
    dropped = sum(round(g / FRAME_MS) - 1 for g in gaps if g > FRAME_MS * 1.5)
    print("%-12s %s" % (name, desc))
    print("  compositor (phoc -> panel; the control, never the app's rate):")
    print("  frames=%d  wall=%.2fs  fps=%.1f  drawn-fps=%.1f" %
          (len(stamps), wall, len(stamps) / wall, 1000.0 / statistics.mean(gaps)))
    print("  frame ms: p50=%.1f p90=%.1f p95=%.1f p99=%.1f max=%.1f" %
          (p(.50), p(.90), p(.95), p(.99), gaps[-1]))
    print("  dropped=%d (%.1f%% of an ideal 60 Hz run)  jank>33ms=%d" %
          (dropped, 100.0 * dropped / (len(gaps) + dropped),
           sum(1 for g in gaps if g > 33.0)))
    if mhz:
        jank = [(i, (stamps[i + 1] - stamps[i]) * 1000.0)
                for i in range(len(stamps) - 1)
                if 33.0 < (stamps[i + 1] - stamps[i]) * 1000.0 < 250.0]
        if jank:
            print("  jank  t+s   ms   MHz      cpu-ms burned during the gap")
            for i, g in jank[:8]:
                d = ("  ".join("%s=%d" % (n, (cpu[i + 1][k] - cpu[i][k]) * 10)
                               for k, n in enumerate(labels))
                     if cpu else "")
                print("       %5.1f %4.0f  %3d>%-3d  %s"
                      % (stamps[i] - stamps[0], g, mhz[i], mhz[i + 1], d))


def measure(name, desc, repeats, fn, wl_log=None):
    """fn(touch) once per repeat; None means don't create a touch device.

    wl_log is a WAYLAND_DEBUG=1 log the app under test is still writing to; the
    run is bracketed by its size, so only this gesture's commits are read.
    """
    # Wake the screen FIRST. The screensaver blanks it after a few minutes
    # idle and an injected touch does not undo that, so an unattended arm
    # otherwise drags against a dark output and lands in the "never changed"
    # branch below -- a void arm that looks identical to a real end-stop.
    woke = unblank()
    # And raise the app, because "the screen is on and unlocked" still is not
    # "the gesture will reach the thing under test". phosh's app grid sits over
    # every window and swallows the drag; on 2026-09-09 an arm flung at the app
    # grid for six repeats and reported a void, with a perfectly healthy browser
    # one surface down. If exactly one app is open there is no ambiguity about
    # which one the arm meant, so raise it.
    tops = toplevels()
    if len(tops) == 1:
        try:
            focus(tops[0].get("app-id"))
        except SystemExit as e:
            print("note: could not raise %s (%s)" % (tops[0].get("app-id"), e),
                  file=sys.stderr)
    # Witness the screen before and after. A run whose screen never changed
    # measured nothing, however many frames it counted, and must say so rather
    # than report a frame rate for a still image.
    before = settle()
    apps = [t.get("app-id") for t in tops] or ["(shell only)"]
    wl_at = os.path.getsize(wl_log) if wl_log else 0
    t = Touch() if fn else None
    s = Sampler()
    s.start()
    time.sleep(0.4)
    t0 = time.monotonic()
    try:
        for _ in range(repeats):
            if fn:
                fn(t)
            else:
                time.sleep(1.0)
    finally:
        wall = time.monotonic() - t0
        s.stop = True
        s.join(1.0)
        if t:
            t.close()
    mid = screen_hash()
    print("%-12s on %s" % (name, ", ".join(apps)))
    if len(s.stamps) < 5 or mid == before:
        print("  NOT MEASURED: %d frames in %.1fs. The screen %s during the "
              "run -- the gesture hit nothing that animates."
              % (len(s.stamps), wall,
                 "never changed" if mid == before else "did change, so the "
                 "frame counter is wrong, not the gesture"))
        if not woke:
            print("  (and the screensaver would not turn the screen on -- "
                  "is there a session on seat0 at all?)")
        return
    report(name, desc, s.stamps, wall, s.mhz, s.cpu, s.proc.labels)
    if wl_log:
        lines = _log_slice(wl_log, wl_at)
        # Keep the slice. The client column below is a summary, and a summary
        # cannot say WHERE a stall was -- ph-wlgaps.py can, but only if it gets
        # the same bytes this saw rather than the whole log with the page load
        # and the settle still in it.
        with open(wl_log + ".drag", "w") as f:
            f.write("\n".join(lines))
        report_client(*client_frames(lines))


def latency(x, y, repeats, idle_s, dy=-200):
    """Cold-start responsiveness: touch-down to first frame on the panel."""
    t = Touch()
    gpu_freq = "/sys/class/devfreq/5000000.gpu/cur_freq"
    out = []
    try:
        for _ in range(repeats):
            time.sleep(idle_s)
            with open(gpu_freq) as f:
                mhz_before = int(f.read()) // 1000000
            v0 = vsync()
            t0 = time.monotonic()
            t.down(x, y)
            t.move(x, y + dy // 5)
            while vsync() == v0:
                if time.monotonic() - t0 > 2.0:
                    break
                time.sleep(0.001)
            out.append(((time.monotonic() - t0) * 1000.0, mhz_before))
            t.move(x, y + dy)
            time.sleep(0.15)
            t.up()
    finally:
        t.close()
    ms = sorted(v[0] for v in out)
    print("latency     touch-down -> first frame, after %.1fs idle" % idle_s)
    print("  samples=%s" % " ".join("%.0fms@%dMHz" % v for v in out))
    print("  p50=%.0fms  worst=%.0fms" % (ms[len(ms) // 2], ms[-1]))


def main():
    argv = sys.argv[1:]
    wl_log = None
    if "--client" in argv:
        i = argv.index("--client")
        wl_log = argv[i + 1]
        del argv[i:i + 2]
        if not os.path.isfile(wl_log):
            # Loudly: an unreadable log is an arm with no client column, and
            # this tool has already shipped one silently-missing number.
            raise SystemExit("--client: no such log: %s (launch the app with "
                             "WAYLAND_DEBUG=1 app 2>%s)" % (wl_log, wl_log))
    if argv and argv[0] == "latency":
        latency(int(argv[1]), int(argv[2]),
                int(argv[3]) if len(argv) > 3 else 5,
                float(argv[4]) if len(argv) > 4 else 3.0,
                int(argv[5]) if len(argv) > 5 else -200)
    elif argv and argv[0] == "drag":
        fling = "--fling" in argv
        argv = [a for a in argv if a != "--fling"]
        # The pause between repeats is idle the client is RIGHT to spend not
        # drawing, and it lands in the frame statistics as a 250-800 ms gap
        # indistinguishable from a stall. `--pause 0` drags back to back, so
        # any gap left over is one.
        pause = 0.7
        if "--pause" in argv:
            i = argv.index("--pause")
            pause = int(argv[i + 1]) / 1000.0
            del argv[i:i + 2]
        x1, y1, x2, y2, ms = (int(v) for v in argv[1:6])
        repeats = int(argv[6]) if len(argv) > 6 else 3
        measure("drag", "%d,%d -> %d,%d in %dms%s, %.0f ms apart"
                % (x1, y1, x2, y2, ms, " then fling" if fling else "",
                   pause * 1000),
                repeats,
                lambda t: (drag(t, x1, y1, x2, y2, ms, fling),
                           time.sleep(pause)),
                wl_log)
    elif argv and argv[0] == "watch":
        measure("watch", "no input, just watching", int(argv[1]), None, wl_log)
    elif argv and argv[0] in SCENES:
        desc, fn = SCENES[argv[0]]
        measure(argv[0], desc, int(argv[1]) if len(argv) > 1 else 3, fn, wl_log)
    else:
        print(__doc__)
        print("scenes: " + ", ".join(SCENES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
