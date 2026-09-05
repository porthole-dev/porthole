#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device; /proc
# env: -
# exits: 0 measured · 1 no process with that comm
"""mtstall.py SECONDS [COMM] -- is the WebKit main thread computing, or blocked?

A frame gap tells you the app missed a deadline; it never tells you whether the
thread was busy or waiting, and those want opposite fixes. This samples the main
thread's utime+stime and run state at 200 Hz and reports its CONTIGUOUS BUSY
RUNS: a 250 ms run means one long computation, a 250 ms idle run means it was
waiting on something else.
"""
import os, sys, time, glob

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
comm = sys.argv[2] if len(sys.argv) > 2 else "WebKitWebProces"
HZ = os.sysconf("SC_CLK_TCK")
TICK_MS = 1000.0 / HZ

pid = None
for d in glob.glob("/proc/[0-9]*"):
    try:
        if open(d + "/comm").read().strip() == comm:
            pid = os.path.basename(d)
            break
    except OSError:
        pass
if not pid:
    sys.exit(f"no process with comm {comm!r}")

stat = f"/proc/{pid}/task/{pid}/stat"   # main thread == the pid's own task
wchan = f"/proc/{pid}/task/{pid}/wchan"
samples = []
end = time.time() + secs
while time.time() < end:
    try:
        s = open(stat).read()
        f = s[s.rindex(") ") + 2:].split()
        samples.append((time.time(), int(f[11]) + int(f[12]), f[0]))
    except (OSError, ValueError, IndexError):
        break
    time.sleep(0.005)

# A sample is "busy" if the tick counter moved since the previous one. At 200 Hz
# and a 100 Hz tick counter that under-reports isolated samples, so runs are
# joined across a single idle sample -- what we are looking for is 100 ms+
# structures, not individual ticks.
runs = []          # (start_t, dur_ms, busy?)
prev_t, prev_ticks = samples[0][0], samples[0][1]
cur_busy, cur_start, gap = None, samples[0][0], 0
for t, ticks, st in samples[1:]:
    busy = ticks > prev_ticks
    if busy:
        gap = 0
    elif cur_busy:
        gap += 1
        if gap <= 2:          # tolerate the 100 Hz counter's granularity
            busy = True
    if cur_busy is None:
        cur_busy = busy
    elif busy != cur_busy:
        runs.append((cur_start, (t - cur_start) * 1000.0, cur_busy))
        cur_start, cur_busy = t, busy
    prev_ticks = ticks
runs.append((cur_start, (samples[-1][0] - cur_start) * 1000.0, cur_busy))

t0 = samples[0][0]
busy_runs = sorted((d for _, d, b in runs if b), reverse=True)
idle_runs = sorted((d for _, d, b in runs if not b), reverse=True)
total_busy = sum(d for _, d, b in runs if b)
print(f"main thread tid={pid}  window={samples[-1][0]-t0:.1f}s  "
      f"on-CPU {total_busy/(samples[-1][0]-t0)/10:.0f}%")
print(f"  longest CONTIGUOUS BUSY runs (ms): "
      f"{' '.join('%.0f' % d for d in busy_runs[:12])}")
print(f"  longest CONTIGUOUS IDLE runs (ms): "
      f"{' '.join('%.0f' % d for d in idle_runs[:12])}")
print(f"  busy runs >100ms: {sum(1 for d in busy_runs if d > 100)}   "
      f"idle runs >100ms: {sum(1 for d in idle_runs if d > 100)}")
print("  timeline of runs >80ms (t+s  ms  state):")
for st, d, b in runs:
    if d > 80:
        print(f"     {st-t0:6.2f}  {d:6.0f}  {'BUSY' if b else 'idle'}")
