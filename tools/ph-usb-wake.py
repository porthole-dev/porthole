#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: soc:qcom
# needs: any (probes state; handles BOOTED and FASTBOOT)
# env: HOST
# exits: 0 ok · non-zero on failure
"""Wake a taimen that is wedged in suspend, from the host, without hands.

A resume-side hang between suspend_late and resume_early leaves the phone with
the watchdog STOPPED -- qcom-wdt.c stops it in its late suspend hook -- so
nothing on the device will ever reset it. Observed 2026-08-02: the phone sat
unresponsive on both links for four minutes in exactly that state, which is the
"needs hands, press power" verdict this project keeps hitting.

It does not need hands. The USB gadget stays enumerated while the system is
suspended, so the host can still reach the port, and a USBDEVFS_RESET on it is
seen by the device side as bus activity. That was enough to get the phone moving
again on every attempt.

    ph-usb-wake.py            # find the gadget by VID:PID and reset it
    ph-usb-wake.py 001 117    # or name the bus/device explicitly

Needs root on the HOST (it writes to /dev/bus/usb), not on the phone -- which is
the point, since the phone is not answering.

This is a recovery tool, not a diagnosis: it tells you nothing about where the
resume hung. Read /var/log/tk-suspend-try.log afterwards for that.
"""
import fcntl
import re
import subprocess
import sys

# Resolve config through the shared lib: this is what supplies $PHONE, the
# mandatory ssh flags (host keys change every boot) and connection
# multiplexing. A tool that builds its own ssh command line gets none of them.
import pathlib as _pl, sys as _sys
for _p in _pl.Path(__file__).resolve().parents:
    if (_p / "lib" / "porthole.py").is_file():
        _sys.path.insert(0, str(_p / "lib"))
        break
import porthole

USBDEVFS_RESET = 0x5514
GADGET = "18d1:d001"   # pmOS gadget on taimen; also what fastboot shows


def find_gadget():
    out = subprocess.run(["lsusb"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if GADGET in line:
            m = re.match(r"Bus (\d+) Device (\d+):", line)
            if m:
                return m.group(1), m.group(2)
    return None


def main():
    if len(sys.argv) == 3:
        bus, dev = sys.argv[1], sys.argv[2]
    else:
        found = find_gadget()
        if not found:
            sys.exit(f"no {GADGET} on the bus -- the phone is not enumerated at "
                     f"all, so this is a reboot or a cable, not a wedged suspend")
        bus, dev = found

    path = f"/dev/bus/usb/{bus}/{dev}"
    with open(path, "wb") as f:
        fcntl.ioctl(f, USBDEVFS_RESET, 0)
    print(f"reset {path} -- give it a few seconds, then try ssh")


if __name__ == "__main__":
    main()
