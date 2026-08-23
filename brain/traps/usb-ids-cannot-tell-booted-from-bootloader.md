---
id: usb-ids-cannot-tell-booted-from-bootloader
title: lsusb can label a running pmOS USB gadget as "fastboot"
scope: generic
subsystem: boot
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §0; profile key PORTHOLE_USB_LIES_AS_FASTBOOT
first-learned: 2026-07-25
---

On taimen, `lsusb` shows the *running* pmOS USB gadget as
`18d1:d001 ... (fastboot)`. It is not fastboot. The device is fully booted.

**So USB IDs cannot discriminate a booted device from a bootloader, and no
decision may key off `lsusb` text.**

The one reliable discriminator is `fastboot devices`, which prints a line
**only** in the real bootloader. `fastboot devices` being empty while `lsusb`
says "fastboot" is the tell.

Set `PORTHOLE_USB_LIES_AS_FASTBOOT=1` and record both IDs in the profile when
you find this on a new device. Check it early: it is cheap to test (boot the
device, run both commands) and expensive to discover halfway through a
debugging session.

Related: [[never-judge-a-boot-by-the-screen]], [[the-lock-says-who-not-what]].
