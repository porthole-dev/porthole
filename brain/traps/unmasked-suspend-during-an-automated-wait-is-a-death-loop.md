---
id: unmasked-suspend-during-an-automated-wait-is-a-death-loop
title: Unmasking suspend before an automated wait can loop a device out of reach
scope: generic
subsystem: power
severity: trap
confidence: proven
evidence: taimen 2026-08-24, bisect2.sh; host dmesg shows re-enumeration every ~20 s then permanent silence
first-learned: 2026-08-24
---

If you are bisecting a suspend bug, your harness will want to unmask
`sleep.target`/`suspend.target` so it can drive a suspend. Unmask it **at the
moment you arm the suspend**, never earlier.

The failure: an arm that reboots, unmasks suspend, then waits for some
condition (a target uptime, a settled service, a temperature) leaves a window
in which the *compositor's own idle timer* suspends the device. That
unscheduled suspend runs under exactly the conditions you were trying to test.
If those conditions are fatal, the device resets, boots, idles, suspends, and
dies again — a loop whose ssh window is a few tens of seconds and which no
polling loop reliably catches.

On taimen this ran until the phone stopped enumerating altogether and needed a
physical 10-second power hold. The host kernel log is the giveaway: repeated

```
usb 1-2: new high-speed USB device number N using xhci_hcd
usb 1-2: USB disconnect, device number N
```

at a roughly fixed interval, then nothing.

Two rules that make this safe:

- Keep suspend masked for the whole setup phase. Unmask, arm the RTC alarm and
  trigger the suspend as one step, and re-mask in the arm's cleanup.
- Run a watcher that masks suspend the instant the device answers ssh again.
  It costs nothing and it is the difference between "one lost arm" and "the
  device is dark until someone walks over to it".

`tools/ph-afk.sh on [duration]` is that mask, and it needs no watcher: it masks
`sleep.target`, `suspend.target` and `systemd-suspend.service`, which are
symlinks in `/etc` and therefore survive the reboot in the middle of your run.
Give it a duration and a transient systemd timer takes the mask off again, so
the phone does not silently stop sleeping for a week. `tools/ph-afk.sh` with no
argument says which state it is in; `off` puts it back.

An inhibitor is the wrong tool here for the same reason: `systemd-inhibit` dies
with the process holding it, and a reboot -- or an ssh session dropping -- is
exactly the window this trap is about.

Related: an unreachable device is usually suspended rather than dead — but if
it is not enumerated on USB *at all*, that is a reboot, a power-off or a cable,
and a USB port reset will not help.
