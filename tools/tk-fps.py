#!/usr/bin/env python3
# scope: soc:msm8998
"""Sample the display/GPU pipeline once a second. Run ON THE DEVICE.

Stdlib only. Everything here is a passive read -- in particular it never opens
/sys/kernel/debug/dri/0/gpu, which runs the GPU crashdumper and wedges the a540
(see HANDOFF-display.md).

Needs root: the frame counter lives in debugfs.

Columns:
  fps     DPU vsync counter deltas, from encoder*/status. This is the real
          number -- one per frame actually latched to the panel, counted by the
          hardware, not by anything in userspace. The panel is 60 Hz cmd mode,
          so 60 is the ceiling and 0 at rest is correct: nothing moving means
          nothing to send.
  undr    underrun count. Nonzero means the DPU missed its fetch deadline --
          the frame went out corrupt or repeated. Should stay 0.
  gpu/s   gpu-irq deltas -- retired GPU submits. 0 while rendering means the
          ring is stranded (the a5xx preemption bug), not merely idle.
  MHz     devfreq cur_freq. 27 means the GPU clock is gated (truly idle).
  phoc%   compositor CPU. A compositor pegged at 100% is CPU-bound, not
          GPU-bound, and no amount of devfreq tuning will help it.

  tk-fps.py [SECONDS] [-q]     default 10
"""
import glob
import os
import re
import sys
import time

IRQS = {"gpu-irq": "gpu"}
# 6.0 named it encoder31, 6.18 names it encoder-0. Glob, don't guess.
ENCODER = next(iter(glob.glob("/sys/kernel/debug/dri/0/encoder*/status")),
               "/sys/kernel/debug/dri/0/encoder-0/status")
DEVFREQ = "/sys/class/devfreq/5000000.gpu/cur_freq"
CLK_TCK = 100


def frame_counts():
    """(vsync, underrun) straight out of the DPU."""
    try:
        with open(ENCODER) as f:
            s = f.read()
        return (int(re.search(r"vsync:\s*(\d+)", s).group(1)),
                int(re.search(r"underrun:\s*(\d+)", s).group(1)))
    except (OSError, AttributeError):
        return (0, 0)


def irq_counts():
    out = {}
    with open("/proc/interrupts") as f:
        for line in f:
            name = line.rsplit("  ", 1)[-1].strip()
            if name in IRQS:
                out[IRQS[name]] = sum(int(x) for x in line.split()[1:9])
    return out


def pid_of(name):
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open("/proc/%s/comm" % pid) as f:
                    if f.read().strip() == name:
                        return int(pid)
            except OSError:
                pass
    return None


def cpu_ticks(pid):
    if pid is None:
        return 0
    try:
        with open("/proc/%d/stat" % pid) as f:
            p = f.read().rsplit(") ", 1)[-1].split()
        return int(p[11]) + int(p[12])
    except (OSError, IndexError, ValueError):
        return 0


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return -1


def main():
    args = [a for a in sys.argv[1:] if a != "-q"]
    quiet = "-q" in sys.argv[1:]
    secs = int(args[0]) if args else 10

    phoc = pid_of("phoc")
    prev, prev_f = irq_counts(), frame_counts()
    prev_cpu, prev_t = cpu_ticks(phoc), time.monotonic()
    rows = []
    if not quiet:
        print("    fps   undr  gpu/s    MHz  phoc%", flush=True)
    for _ in range(secs):
        time.sleep(1.0)
        now, f = irq_counts(), frame_counts()
        cpu, t = cpu_ticks(phoc), time.monotonic()
        dt = t - prev_t
        d = {k: (now.get(k, 0) - prev.get(k, 0)) / dt for k in IRQS.values()}
        d["fps"] = (f[0] - prev_f[0]) / dt
        d["undr"] = f[1] - prev_f[1]
        d["mhz"] = read_int(DEVFREQ) / 1e6
        d["cpu"] = (cpu - prev_cpu) / CLK_TCK / dt * 100
        rows.append(d)
        if not quiet:
            print("%7.1f%7.0f%7.0f%7.0f%7.0f"
                  % (d["fps"], d["undr"], d["gpu"], d["mhz"], d["cpu"]),
                  flush=True)
        prev, prev_f = now, f
        prev_cpu, prev_t = cpu, t

    if rows:
        n = len(rows)
        avg = {k: sum(r[k] for r in rows) / n for k in rows[0]}
        busy = [r for r in rows if r["fps"] > 1]
        print("AVG %6.1f%7.0f%7.0f%7.0f%7.0f   busy-fps=%.1f over %d/%d s"
              % (avg["fps"], avg["undr"], avg["gpu"], avg["mhz"], avg["cpu"],
                 sum(r["fps"] for r in busy) / len(busy) if busy else 0,
                 len(busy), n),
              flush=True)


if __name__ == "__main__":
    main()
