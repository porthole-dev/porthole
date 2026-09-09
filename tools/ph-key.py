#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 2 usage
"""Inject a key press through /dev/uinput. Run ON THE DEVICE as root.

Same idiom as tools/ph-touch.py -- plain ioctl + struct, no ctypes, no deps.
Exists because the panel is owned by the compositor: with the output asleep
there is no wlopm here, and writing the backlight does nothing while the DRM
connector is disabled. A power-key press is the path the compositor listens on.

  ph-key.py power        one KEY_POWER press/release
  ph-key.py type 123456  type digits
  ph-key.py type -       read the digits from stdin instead of argv
  ph-key.py enter        one KEY_ENTER
  ph-key.py esc          one KEY_ESC -- what closes phosh's overview, and
                         an arm that starts in the overview flings at the
                         app grid and reports a void

NEVER pass a real lock-screen PIN as an argument. It lands in `ps`, in shell
history, and -- because /dev/uinput needs root -- in journald, which logs
sudo's whole command line: `COMMAND=/usr/bin/python3 ph-key.py type <pin>`
is then on disk in plaintext for as long as the journal is kept. Pipe it:

    printf %s "$PIN" | sudo python3 ph-key.py type -
"""
import fcntl, os, struct, sys, time

EV_SYN, EV_KEY = 0x00, 0x01
KEY_POWER, KEY_ENTER, KEY_ESC = 116, 28, 1
# KEY_1..KEY_9 are 2..10, KEY_0 is 11
DIGITS = {str(d): d + 1 for d in range(1, 10)}
DIGITS["0"] = 11
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502

fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
for _k in [KEY_POWER, KEY_ENTER, KEY_ESC] + list(DIGITS.values()):
    fcntl.ioctl(fd, UI_SET_KEYBIT, _k)
# struct uinput_user_dev: name[80] + id{bustype,vendor,product,version} + ff + abs arrays
name = b"tk-key".ljust(80, b"\0")
os.write(fd, name + struct.pack("HHHH", 0x03, 0x1209, 0x0001, 1) + struct.pack("i", 0)
         + b"\0" * (4 * 64 * 4))
fcntl.ioctl(fd, UI_DEV_CREATE)
time.sleep(0.3)

def ev(t, c, v):
    os.write(fd, struct.pack("llHHi", 0, 0, t, c, v))

def press(code, hold=0.05):
    ev(EV_KEY, code, 1); ev(EV_SYN, 0, 0)
    time.sleep(hold)
    ev(EV_KEY, code, 0); ev(EV_SYN, 0, 0)
    time.sleep(0.09)

cmd = sys.argv[1] if len(sys.argv) > 1 else "power"
if cmd == "power":
    press(KEY_POWER, 0.08)
elif cmd == "enter":
    press(KEY_ENTER)
elif cmd == "esc":
    press(KEY_ESC)
elif cmd == "type":
    arg = sys.argv[2] if len(sys.argv) > 2 else "-"
    digits = sys.stdin.readline().strip() if arg == "-" else arg
    for ch in digits:
        if ch in DIGITS:
            press(DIGITS[ch])
    press(KEY_ENTER)
else:
    print("usage: ph-key.py power|enter|esc|type DIGITS"); sys.exit(2)
time.sleep(0.3)
fcntl.ioctl(fd, UI_DEV_DESTROY)
os.close(fd)
print(f"sent: {cmd}")
