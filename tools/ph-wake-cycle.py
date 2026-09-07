#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (run it on the device as root, e.g. pushed by ph-stream.sh)
# env: TK_GAP_MS
# exits: 0 ran to the end · 2 could not open /dev/uinput
"""Press the power key on a loop and count the blank/unblank transitions that
really happened. THE oracle for the display-wake GPU crash.

That failure is roughly one wake in twenty and usually leaves nothing behind --
often not even a panic, just a watchdog reset -- so the only way to tell a fix
from luck is to count. Baseline on taimen dies by cycle ~24; the immune arms ran
100 and 118. Anything under ~60 is noise, not a fix.

Three things this does that a shell loop does not:

  - it counts TRANSITIONS, not presses. A press the compositor swallowed is not
    a wake, and counting it inflates the score of whatever is being tested.
  - it keeps ONE uinput device open for the whole run. A device created per
    press is a hotplug event the compositor reacts to, and is not what the
    hardware key looks like.
  - every line goes to /dev/kmsg as well as stdout, so netconsole carries the
    count off the phone. When the SoC stops answering, the last count that
    arrived IS the result -- stdout over ssh does not survive the reset.

The control is on every line: gpu= is the GPU's runtime status read just before
the press. The crash needs the GPU to have power-collapsed first, so a run whose
gpu= never reads "suspended" has not exercised the bug at all and its clean
score means nothing. The verdict line reports how many cycles had it.

Usage (on the device):  sudo python3 -u ph-wake-cycle.py [cycles]
Usage (from the host):  TK_PUSH=tools/ph-wake-cycle.py \
                          tools/ph-stream.sh logs/cycle.log \
                          sudo python3 -u /tmp/ph-wake-cycle.py 100
"""
import fcntl, glob, os, struct, sys, time


def one(pattern, what):
    """The single path matching `pattern`. Nothing here may be hardcoded: card
    numbering and the connector name differ per device and per boot."""
    hits = sorted(glob.glob(pattern))
    if len(hits) != 1:
        sys.exit("expected exactly one %s, found %d: %s" % (what, len(hits), hits))
    return hits[0]


# Discovered, not hardcoded: the card number and the connector name are
# per-device. A device with two connectors gets a clear error, not a guess.
PANEL = one("/sys/class/drm/card*/card*-*/enabled", "connected panel")
GPU = one("/sys/devices/platform/*/*.gpu/power/runtime_status", "GPU device")

EV_SYN, EV_KEY, KEY_POWER = 0x00, 0x01, 116
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502

CYCLES = int(sys.argv[1]) if len(sys.argv) > 1 else 100
# 2 s, because the GPU's autosuspend is 200 ms: any shorter and presses start
# landing on a GPU that never power-collapsed, which is a different experiment
# (a mash) and does not reproduce this bug the same way.
GAP = int(os.environ.get("TK_GAP_MS", "2000")) / 1000
SETTLE = 4.0            # give-up point for one transition


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError as e:
        return "err:%s" % e.errno


def say(msg):
    print(msg, flush=True)
    try:
        with open("/dev/kmsg", "w") as f:
            f.write("tk-wake-cycle: %s\n" % msg)
    except OSError:
        pass


try:
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
except OSError as e:
    sys.exit("cannot open /dev/uinput (run as root): %s" % e)
fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
fcntl.ioctl(fd, UI_SET_KEYBIT, KEY_POWER)
# struct uinput_user_dev: name[80] + id{bustype,vendor,product,version} + ff + abs arrays
os.write(fd, b"tk-wake-cycle".ljust(80, b"\0")
         + struct.pack("HHHH", 0x03, 0x1209, 0x0001, 1) + struct.pack("i", 0)
         + b"\0" * (4 * 64 * 4))
fcntl.ioctl(fd, UI_DEV_CREATE)
time.sleep(0.3)         # the compositor has to enumerate it before it will listen


def press():
    for v in (1, 0):
        os.write(fd, struct.pack("llHHi", 0, 0, EV_KEY, KEY_POWER, v))
        os.write(fd, struct.pack("llHHi", 0, 0, EV_SYN, 0, 0))
        time.sleep(0.08)


def wait_for(state, deadline):
    """Poll the panel until it reads `state`, or the deadline passes."""
    while time.monotonic() < deadline:
        if read(PANEL) == state:
            return True
        time.sleep(0.05)
    return False


done = missed = collapsed = 0
say("start cycles=%d gap=%.1fs panel=%s" % (CYCLES, GAP, read(PANEL)))
while done < CYCLES:
    before, gpu = read(PANEL), read(GPU)
    if gpu == "suspended":
        collapsed += 1
    want = "disabled" if before == "enabled" else "enabled"
    press()
    if wait_for(want, time.monotonic() + SETTLE):
        done += 1
        say("cycle %d %s->%s gpu=%s" % (done, before, want, gpu))
    else:
        missed += 1
        say("MISSED press (panel stayed %s, gpu=%s) missed=%d" % (before, gpu, missed))
        if missed > CYCLES // 4:
            say("VERDICT aborted: %d presses swallowed -- is the session alive?" % missed)
            break
    time.sleep(GAP)

say("VERDICT survived %d transitions, %d with the GPU collapsed, %d presses missed"
    % (done, collapsed, missed))
fcntl.ioctl(fd, UI_DEV_DESTROY)
os.close(fd)
