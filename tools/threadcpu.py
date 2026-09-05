#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device; /proc
# env: -
# exits: 0 measured · 1 no process matched
"""threadcpu.py [SECONDS] [PROC-MATCH] -- per-thread CPU of a multithreaded app.

A whole-process CPU number cannot answer "is the work parallel?", and on a
browser that is the question that decides what to optimise. This prints every
thread that used more than a trace of a core over the window, busiest first.

It is how the 2026-09-05 scroll campaign killed the "raise the Skia paint
threads" plan in ten minutes instead of a 56-minute WebKit build: during a
drag the two SkiaCPUWorker threads use 3-15% of a core while the main thread
uses 50-65%. Rasterisation was never the bottleneck.

    threadcpu.py 8                    # 8 s of WebKitWebProcess
    threadcpu.py 5 phoc               # any process, by cmdline substring
"""
import os
import sys
import time
import glob

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
match = sys.argv[2] if len(sys.argv) > 2 else "WebKitWebProcess"
HZ = os.sysconf("SC_CLK_TCK")


def find():
    """First non-bwrap process whose cmdline contains `match`.

    The bwrap filter is not cosmetic: WebKit's sandbox means two `bwrap ... --
    WebKitWebProcess` wrappers match the name before the real process does, and
    both are idle. Reporting one of them prints a confident "total 0% of one
    core" for a browser that is busy.
    """
    for d in glob.glob("/proc/[0-9]*"):
        try:
            cl = open(d + "/cmdline").read().replace("\0", " ")
            if match in cl and "bwrap" not in cl:
                return d
        except OSError:
            pass
    return None


def sample(p):
    out = {}
    for t in glob.glob(p + "/task/[0-9]*"):
        try:
            st = open(t + "/stat").read()
            fields = st[st.rindex(")") + 2:].split()
            out[os.path.basename(t)] = (int(fields[11]) + int(fields[12]),
                                        open(t + "/comm").read().strip())
        except (OSError, ValueError, IndexError):
            pass
    return out


p = find()
if not p:
    sys.exit(f"no process matching {match!r}")
a = sample(p)
t0 = time.monotonic()
time.sleep(secs)
b = sample(p)
dt = time.monotonic() - t0

rows = []
for tid, (ticks, name) in b.items():
    if tid in a:
        pct = (ticks - a[tid][0]) / HZ / dt * 100
        if pct > 0.05:
            rows.append((pct, name, tid))
rows.sort(reverse=True)
print(f"pid={os.path.basename(p)} window={dt:.1f}s  "
      f"total={sum(r[0] for r in rows):.0f}% of one core")
for pct, name, tid in rows:
    print(f"  {pct:6.1f}%  {name:<20} tid={tid}")
