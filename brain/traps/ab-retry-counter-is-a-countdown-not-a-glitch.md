---
id: ab-retry-counter-is-a-countdown-not-a-glitch
title: "Every Nth boot lands in the bootloader" is a retry countdown, not a glitch
scope: generic
subsystem: boot
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §2; profile key PORTHOLE_BOOT_RETRIES
first-learned: 2026-07-26
---

On an A/B device, `fastboot set_active <slot>` arms that slot with a fixed
number of boot retries (3 on taimen) and clears `unbootable`.

**Nothing in pmOS ever reports a boot successful.** So the bootloader scores
*every* boot as failed and decrements the counter. At 0 it stops handing off and
keeps the device.

That is the entire "roughly every 3rd boot lands in the bootloader by itself"
cadence. It is a countdown, not a glitch, and it is also the only reliable way
*into* the bootloader from software if the syscall path is unavailable.

**Always `set_active` before rebooting out of the bootloader.** `set_active`
resets the counter and clears `unbootable`; a bare `fastboot reboot` from a
counter already at 0 drops straight back into the bootloader. That is the single
most common "the phone is stuck in fastboot" confusion, and it looks like a
hardware fault.

Record `PORTHOLE_BOOT_RETRIES` in the profile so the number is discoverable, and
`PORTHOLE_SLOT_FORBIDDEN` for any slot with no known-good image — porthole
refuses to `set_active` that one rather than trusting you to remember at 3am.

Related: [[busybox-reboot-eats-the-mode-string]], [[dtbo-must-match-the-kernel]].
