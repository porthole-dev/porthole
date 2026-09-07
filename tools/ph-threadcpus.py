#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device; a running process to watch
# env: -
# exits: 0 ok · 1 the process was not found
"""Which CPU does each of a process's threads actually run on, and at what clock.

On an asymmetric SoC "the main thread is 48% busy" is not a number until you
know WHICH core it was 48% of. A little core at 1.9 GHz and a big one at
2.36 GHz differ by more than that on integer work -- msm8998 reports
capacity 549 vs 1024 -- so a UI thread parked on the little cluster is late
for a reason no CPU-percentage or flat profile can show.

Reads field 39 of /proc/<tid>/stat, the last CPU the thread ran on, which is
sampled rather than exact: a thread that migrates between samples is counted
where it was seen. That is fine for "does this thread live on the big
cluster", which is the question.

  ph-threadcpus.py PID [SECONDS] [INTERVAL_MS]
"""
import collections
import glob
import os
import sys
import time


def read_policies():
    out = []
    for p in sorted(glob.glob("/sys/devices/system/cpu/cpufreq/policy*/")):
        try:
            with open(p + "affected_cpus") as f:
                cpus = [int(c) for c in f.read().split()]
            out.append((p, cpus))
        except (OSError, ValueError):
            pass
    return out


def cur_khz(path):
    try:
        with open(path + "scaling_cur_freq") as f:
            return int(f.read())
    except (OSError, ValueError):
        return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    pid = int(sys.argv[1])
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    interval = (float(sys.argv[3]) if len(sys.argv) > 3 else 20.0) / 1000.0
    if not os.path.isdir("/proc/%d/task" % pid):
        print("no such process: %d" % pid, file=sys.stderr)
        return 1

    caps = {}
    for c in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpu_capacity"):
        # basename of the DIRECTORY. Splitting the whole path on "/cpu" finds
        # the "/sys/devices/system/cpu/" one first and yields "", so every
        # capacity was silently dropped and every thread read "100% on big".
        try:
            with open(c) as f:
                caps[int(os.path.basename(os.path.dirname(c))[3:])] = int(f.read())
        except (OSError, ValueError, IndexError):
            pass
    big = max(caps.values()) if caps else 0

    policies = read_policies()
    seen = collections.defaultdict(collections.Counter)
    names, freqs = {}, collections.defaultdict(list)
    end = time.monotonic() + secs
    while time.monotonic() < end:
        for t in os.listdir("/proc/%d/task" % pid):
            try:
                with open("/proc/%s/task/%s/stat" % (pid, t)) as f:
                    fields = f.read().rsplit(") ", 1)
                # Only a RUNNING thread's CPU is worth counting: a sleeping one
                # reports wherever it last ran and would pile up samples on a
                # core it has not used for seconds.
                rest = fields[-1].split()
                if rest[0] != "R":
                    continue
                seen[t][int(rest[36])] += 1
                if t not in names:
                    with open("/proc/%s/task/%s/comm" % (pid, t)) as f:
                        names[t] = f.read().strip()
            except (OSError, IndexError, ValueError):
                continue
        for path, cpus in policies:
            freqs[cpus[0]].append(cur_khz(path))
        time.sleep(interval)

    print("thread CPU residency for pid %d over %.0f s "
          "(only samples where the thread was RUNNING)" % (pid, secs))
    print("  %-18s %7s %7s  %s" % ("thread", "samples", "on big", "per-cpu"))
    for t, hist in sorted(seen.items(), key=lambda kv: -sum(kv[1].values()))[:10]:
        n = sum(hist.values())
        onbig = sum(v for c, v in hist.items() if caps.get(c) == big)
        print("  %-18s %7d %6.0f%%  %s"
              % (names.get(t, t)[:18], n, 100.0 * onbig / n,
                 dict(sorted(hist.items()))))
    for c0, series in sorted(freqs.items()):
        if series:
            s = sorted(series)
            print("  policy%d MHz: p50=%d p90=%d max=%d"
                  % (c0, s[len(s) // 2] // 1000,
                     s[int(len(s) * .9)] // 1000, s[-1] // 1000))
    return 0


if __name__ == "__main__":
    sys.exit(main())
