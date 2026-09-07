#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device; CONFIG_UCLAMP_TASK=y; same uid as the target (no root)
# env: -
# exits: 0 applied to at least one thread · 1 nothing matched · 64 usage
"""Raise the scheduler's utilization floor for a running app's threads.

WHY THIS EXISTS: on an asymmetric SoC with **no energy model** -- which is the
normal state of a mainline port, `/sys/kernel/debug/energy_model` empty -- EAS
is off, so the scheduler places threads by plain load balancing and only moves
one to a big core once it is already a "misfit". A UI thread that must finish
inside 16.7 ms is late by the time that happens. Measured on msm8998: WebKit's
ThreadedCompositor spent 65% of its running samples on the LITTLE cluster and
the big cluster idled at a p50 of 1420 MHz out of 2361.

uclamp_min fixes both halves at once: it is the floor schedutil uses to pick a
frequency AND the value misfit detection compares, so a boosted thread both
runs faster and gets moved up sooner. Per-task via sched_setattr(2), which
needs no root and no cgroup cpu controller -- Alpine's systemd here is built
without cpu-controller support, so cpu.uclamp.min does not exist and the cgroup
route is closed. See /usr/libexec/taimen-uclamp-session, whose verified syscall
recipe this reuses.

  ph-uclamp.py MIN COMM [COMM...]     every thread of every process named COMM
  ph-uclamp.py MIN --pid PID          every thread of one process

MIN is 0-1024, where 1024 is "as big as the biggest CPU". 0 removes the boost.
Process names come from /proc/<pid>/comm and the kernel truncates them to 15
characters, so it is "WebKitWebProces", not "WebKitWebProcess" -- pass what
`cat /proc/<pid>/comm` prints, and this refuses a name too long to ever match.
"""
import ctypes
import os
import sys

SYS_sched_setattr = 274  # arm64, native 64-bit table
SCHED_FLAG_KEEP_ALL = 0x08 | 0x10          # KEEP_POLICY | KEEP_PARAMS
SCHED_FLAG_UTIL_CLAMP_MIN = 0x20
COMM_MAX = 15


class sched_attr(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("sched_policy", ctypes.c_uint32),
                ("sched_flags", ctypes.c_uint64), ("sched_nice", ctypes.c_int32),
                ("sched_priority", ctypes.c_uint32),
                ("sched_runtime", ctypes.c_uint64),
                ("sched_deadline", ctypes.c_uint64),
                ("sched_period", ctypes.c_uint64),
                ("sched_util_min", ctypes.c_uint32),
                ("sched_util_max", ctypes.c_uint32)]


_libc = ctypes.CDLL(None, use_errno=True)


def set_min(tid, util_min):
    attr = sched_attr()
    attr.size = ctypes.sizeof(sched_attr)
    attr.sched_flags = SCHED_FLAG_KEEP_ALL | SCHED_FLAG_UTIL_CLAMP_MIN
    attr.sched_util_min = util_min
    ctypes.set_errno(0)
    if _libc.syscall(SYS_sched_setattr, ctypes.c_int(tid),
                     ctypes.byref(attr), ctypes.c_uint(0)) == 0:
        return 0
    return ctypes.get_errno()


def comm(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def pids_named(names):
    out = []
    for entry in os.listdir("/proc"):
        if entry.isdigit() and comm("/proc/%s/comm" % entry) in names:
            out.append(entry)
    return out


def main():
    argv = sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        return 64
    util_min = int(argv[0])
    if not 0 <= util_min <= 1024:
        print("MIN must be 0-1024", file=sys.stderr)
        return 64
    if argv[1] == "--pid":
        pids = [argv[2]]
    else:
        names = set(argv[1:])
        # A name longer than the kernel's own field can never match, and the
        # silent empty result reads as "the app was not running".
        too_long = [n for n in names if len(n) > COMM_MAX]
        if too_long:
            print("comm is truncated to %d chars by the kernel; %s can never "
                  "match -- use %r" % (COMM_MAX, ", ".join(too_long),
                                       too_long[0][:COMM_MAX]), file=sys.stderr)
            return 64
        pids = pids_named(names)
    if not pids:
        print("no process matched", file=sys.stderr)
        return 1

    done = failed = 0
    for pid in pids:
        try:
            tids = os.listdir("/proc/%s/task" % pid)
        except OSError:
            continue
        for tid in tids:
            err = set_min(int(tid), util_min)
            if err:
                failed += 1
                if failed <= 3:
                    print("  tid %s (%s): errno %d"
                          % (tid, comm("/proc/%s/task/%s/comm" % (pid, tid)), err),
                          file=sys.stderr)
            else:
                done += 1
    print("uclamp_min=%d on %d threads of %s (%d refused)"
          % (util_min, done, ",".join(pids), failed))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
