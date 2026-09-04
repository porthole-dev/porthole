---
id: androidboot-bootreason-always-says-watchdog-here
title: androidboot.bootreason says watchdog on every boot of taimen, including clean ones -- it is not a reset-reason oracle
scope: device:google-taimen
subsystem: boot
severity: trap
confidence: proven
evidence: read from /proc/cmdline after an unexplained hard reset and again after a deliberate `reboot` from ssh, 2026-09-04; identical both times
first-learned: 2026-09-04
---

**Do not** read `androidboot.bootreason` out of `/proc/cmdline` as the reason
the phone went down. On taimen it says `watchdog` **every time**, including
after a clean `reboot` typed over ssh.

This is a tempting instrument precisely because there is no other one:
[[ram-does-not-survive-a-reset-here]] means pstore comes back empty from every
reset, so a hard reset normally leaves a journal that stops mid-line and
nothing else. A cmdline field that survives -- because the bootloader writes
it -- looks like the answer. It is a constant.

The mechanism is in the same cmdline, two fields earlier:

    qcom_wdt.arm_on_probe=1 watchdog.open_timeout=300 reboot=w

`reboot=w` puts the kernel in watchdog reboot mode: an ordinary shutdown ends
by letting the qcom watchdog bite, so the bootloader records `watchdog` for a
deliberate reboot exactly as it does for a real hard lockup. The field
describes *how* the SoC was reset, not *why*.

**Instead**: separate a crash from a reboot with things that do differ --
`uptime` on the far side, whether the previous boot's journal ends with a
shutdown sequence or stops mid-line
(`journalctl -b -1 -n 40`), and host-side liveness during the event the way
`tools/tk-hang-matrix.sh` does it. For a hang you intend to catch, arm a
witness before it happens; there is no post-hoc one here.
