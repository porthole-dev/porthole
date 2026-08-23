#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Log the display pipeline so a frozen screen can be read off afterwards.

Run ON THE DEVICE, as root. Stdlib only. One line per state change; redirect it
to a file and leave it running while reproducing the freeze.

*** DO NOT READ /sys/kernel/debug/dri/0/gpu ON THIS DEVICE. ***

Opening that file is not a passive read: msm_gpu_open() calls
a5xx_gpu_state_get(), which allocates a 1 MB buffer in the GPU's GLOBAL address
space and runs the GPU crashdumper into it. That aspace is based at 2^48, the
A540 truncates addresses to 48 bits, and the crashdumper's write therefore lands
on an unmapped low address -- faulting the GPU and wedging it for good, because
recovery fails as well.

This tool used to poll that file twice a second and so manufactured the exact
faults it was written to observe. Hours went into investigating them. The fence
counters it used to read are gone for that reason; what is left below touches
nothing that can perturb the GPU.

What is left is phoc's and phrog's CPU time plus the touch irq count. A
compositor that is awake and burning cycles but drawing nothing looks very
different from one blocked in an atomic commit waiting on a page flip, and
wchan tells those apart without touching the GPU at all.

  tk-display-watch.py [SECONDS]        default 3600
"""
import os
import sys
import time

SAMPLE_S = 0.5
HEARTBEAT_S = 20.0


def pid_of(name):
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/comm" % pid) as f:
                if f.read().strip() == name:
                    return int(pid)
        except OSError:
            pass
    return None


def cpu_ticks(pid):
    if pid is None:
        return -1
    try:
        with open("/proc/%d/stat" % pid) as f:
            parts = f.read().split()
        return int(parts[13]) + int(parts[14])
    except (OSError, IndexError, ValueError):
        return -1


def wchan(pid):
    if pid is None:
        return "-"
    try:
        with open("/proc/%d/wchan" % pid) as f:
            return f.read().strip() or "-"
    except OSError:
        return "-"


def irq_count():
    try:
        with open("/proc/interrupts") as f:
            for line in f:
                if "ftm4" in line:
                    return sum(int(x) for x in line.split()[1:9])
    except (OSError, ValueError):
        pass
    return -1


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 3600.0
    boot = time.clock_gettime(time.CLOCK_MONOTONIC)

    phoc = pid_of("phoc")
    phrog = pid_of("phrog")

    last_key = None
    last_report = -HEARTBEAT_S
    print("# t  phoc_ticks phrog_ticks irq  phoc_wchan "
          "(GPU debugfs deliberately NOT read -- see module docstring)")
    sys.stdout.flush()

    end = time.time() + secs
    while time.time() < end:
        # The compositor is restarted by greetd on login, so re-resolve.
        if phoc is None or not os.path.exists("/proc/%d" % phoc):
            phoc = pid_of("phoc")
        if phrog is None or not os.path.exists("/proc/%d" % phrog):
            phrog = pid_of("phrog")

        pt, gt, irq = cpu_ticks(phoc), cpu_ticks(phrog), irq_count()
        now = time.clock_gettime(time.CLOCK_MONOTONIC) - boot

        key = (pt, gt, irq)
        changed = key != last_key
        if changed or now - last_report >= HEARTBEAT_S:
            print("%9.2f  phoc=%d phrog=%d irq=%d  %s"
                  % (now, pt, gt, irq, wchan(phoc)))
            sys.stdout.flush()
            last_report = now
            last_key = key

        time.sleep(SAMPLE_S)


if __name__ == "__main__":
    main()
