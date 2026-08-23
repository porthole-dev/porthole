#!/usr/bin/env python3
# scope: generic
"""Measure how smooth the phosh session actually is. Run ON THE DEVICE as root.

Average FPS is the wrong number. A session that renders 58 frames in a second
but stalls 100 ms in the middle of a scroll reads as broken, while a steady 45
reads as fine. So this samples the DPU's own vsync counter fast enough to
recover *individual frame intervals* and reports their distribution: p50 is
what the session normally does, p95/max is what the user actually notices.

Needs tk-touch.py beside it.

  tk-gesture-bench.py NAME [REPEATS]
  tk-gesture-bench.py drag X1 Y1 X2 Y2 MS [REPEATS] [--fling]
  tk-gesture-bench.py watch SECONDS          measure without touching anything
  tk-gesture-bench.py latency X Y [REPEATS] [IDLE_S]
        Idle until the GPU and DPU have powered down, then touch and time the
        first frame out. This is the number a user calls "laggy": average FPS
        says nothing about how long the screen sits still after a finger lands.

NAME is one of the gestures in SCENES below.
"""
import statistics
import sys
import threading
import glob
import time

sys.path.insert(0, "/tmp")
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from tk_touch import Touch, drag  # noqa: E402
from tk_ui import screen_hash, settle, toplevels  # noqa: E402

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


def measure(name, desc, repeats, fn):
    """fn(touch) once per repeat; None means don't create a touch device."""
    # Witness the screen before and after. A run whose screen never changed
    # measured nothing, however many frames it counted, and must say so rather
    # than report a frame rate for a still image.
    before = settle()
    apps = [t.get("app-id") for t in toplevels()] or ["(shell only)"]
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
        return
    report(name, desc, s.stamps, wall, s.mhz, s.cpu, s.proc.labels)


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
    if argv and argv[0] == "latency":
        latency(int(argv[1]), int(argv[2]),
                int(argv[3]) if len(argv) > 3 else 5,
                float(argv[4]) if len(argv) > 4 else 3.0,
                int(argv[5]) if len(argv) > 5 else -200)
    elif argv and argv[0] == "drag":
        fling = "--fling" in argv
        argv = [a for a in argv if a != "--fling"]
        x1, y1, x2, y2, ms = (int(v) for v in argv[1:6])
        repeats = int(argv[6]) if len(argv) > 6 else 3
        measure("drag", "%d,%d -> %d,%d in %dms%s"
                % (x1, y1, x2, y2, ms, " then fling" if fling else ""),
                repeats,
                lambda t: (drag(t, x1, y1, x2, y2, ms, fling), time.sleep(0.7)))
    elif argv and argv[0] == "watch":
        measure("watch", "no input, just watching", int(argv[1]), None)
    elif argv and argv[0] in SCENES:
        desc, fn = SCENES[argv[0]]
        measure(argv[0], desc, int(argv[1]) if len(argv) > 1 else 3, fn)
    else:
        print(__doc__)
        print("scenes: " + ", ".join(SCENES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
