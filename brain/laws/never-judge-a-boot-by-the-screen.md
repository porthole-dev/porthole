---
id: never-judge-a-boot-by-the-screen
title: Never judge a boot by the screen
scope: generic
subsystem: boot
severity: law
confidence: proven
evidence: taimen README.md "Two traps that cost whole sessions"; profile key PORTHOLE_PANEL_STAYS_DARK
first-learned: 2026-07-25
---

Mainline does not light the panel until display bring-up lands, which is
typically late. Until then **a fully booted device and a hung one look
identical**: dark, or holding whatever frame the bootloader last painted — a
vendor logo, a charging icon.

Judge a boot by a channel that exists before the display does:

```sh
ip addr                 # did the USB gadget come up on the host?
fastboot devices        # a line here means the bootloader, not your kernel
cat /proc/version       # which build actually answered
```

Set `PORTHOLE_PANEL_STAYS_DARK=1` in the profile while this is true, so tools
and future readers know the screen carries no information.

The inverse trap is worse: a device that *does* paint something can be showing
you the bootloader's last frame from a kernel that panicked seconds ago.

Related: [[usb-ids-cannot-tell-booted-from-bootloader]],
[[prove-which-kernel-answered]].
