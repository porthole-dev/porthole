#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: on-device (run it on the phone as root; ph-dt2w-test.sh ships it there)
# env: -
# exits: 0 double tap seen · 1 armed, none seen · 65 nobody touched the glass · 70 setup failed
"""The device half of ph-dt2w-test.sh: wait for a finger, arm, watch for a tap.

Runs ON THE PHONE, stdlib only. It lives in a file rather than in a shell
one-liner because the host-side version of this was an awk program inside an
`sh -c '...'` inside ssh, and busybox ash choked on the nested quotes --
printing a syntax error that the caller then reported as "nobody touched the
phone in 600s". A harness failure must never look like a device result.

Phase 1 is the positive control and has to come FIRST, unarmed: with gesture
mode off, any touch raises the ftm4 interrupt, so waiting for that is how we
learn a finger is present. Doing it while armed proves nothing -- the
controller is SUPPOSED to stay silent there for a single tap.
"""
import os
import re
import struct
import sys
import time

PARAM = "/sys/module/ftm4/parameters/gesture_test"
NAME = "stmicroelectronics ftm4"
KEY_WAKEUP = 143


def irq_count():
    """Total ftm4 interrupts across all CPUs, or None if the line is gone.

    EXACTLY one column per CPU, from the header. Summing every numeric field
    instead reads the pin number out of "msmgpio 125 Level ftm4" as 125
    interrupts -- which made a dead counter look busy on 2026-09-20.
    """
    ncpus = 0
    with open("/proc/interrupts") as f:
        for line in f:
            if not ncpus:
                head = line.split()
                if head and all(c.startswith("CPU") for c in head):
                    ncpus = len(head)
                continue
            if ":" not in line:
                continue
            label, rest = line.split(":", 1)
            fields = rest.split()
            if len(fields) < ncpus or not all(c.isdigit() for c in fields[:ncpus]):
                continue
            if fields[ncpus:] and fields[-1] == "ftm4":
                return sum(int(c) for c in fields[:ncpus])
    return None


def event_node():
    """The ftm4 evdev node, by NAME. Never by index -- it moves with probe order."""
    found = False
    with open("/proc/bus/input/devices") as f:
        for line in f:
            if line.startswith("N: Name="):
                found = NAME in line
            elif found and line.startswith("H: Handlers="):
                m = re.search(r"event\d+", line)
                if m:
                    return "/dev/input/" + m.group(0)
    return None


def main():
    wait = int(sys.argv[1]) if len(sys.argv) > 1 else 600

    if not os.path.exists(PARAM):
        print("SETUP: this kernel has no ftm4.gesture_test", flush=True)
        return 70
    node = event_node()
    if not node:
        print("SETUP: no ftm4 input device", flush=True)
        return 70
    base = irq_count()
    if base is None:
        print("SETUP: no ftm4 line in /proc/interrupts", flush=True)
        return 70

    print("READY %s irq=%d" % (node, base), flush=True)
    print("PHASE1 touch the screen once -- nothing is armed yet", flush=True)

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        now = irq_count()
        if now is not None and now != base:
            print("FINGER irq %d -> %d" % (base, now), flush=True)
            break
        time.sleep(0.5)
    else:
        print("NOBODY nothing touched the glass in %ds" % wait, flush=True)
        return 65

    try:
        with open(PARAM, "w") as f:
            f.write("1")
    except OSError as e:
        print("SETUP: could not arm gesture mode: %s" % e, flush=True)
        return 70
    print("ARMED double-tap the middle of the screen, repeatedly", flush=True)

    wake = other = 0
    try:
        # Unbuffered, and O_NONBLOCK so the deadline is ours and not read()'s.
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                buf = os.read(fd, 24)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            if len(buf) < 24:
                continue
            _s, _us, etype, code, value = struct.unpack("QQHHi", buf)
            if not etype:
                continue
            print("EV type=%d code=%d value=%d" % (etype, code, value), flush=True)
            if etype == 1 and code == KEY_WAKEUP and value == 1:
                wake += 1
                break
            other += 1
        os.close(fd)
    finally:
        try:
            with open(PARAM, "w") as f:
                f.write("0")
        except OSError:
            print("WARNING could not disarm gesture mode -- touch stays dead",
                  flush=True)

    if wake:
        print("RESULT double tap reported", flush=True)
        return 0
    print("RESULT no double tap; %d other event(s) while armed" % other, flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
