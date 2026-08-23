---
id: dtbo-must-match-the-kernel
title: The dtbo must match the kernel, and the bootloader reads it from the active slot
scope: generic
subsystem: boot
severity: trap
confidence: proven
evidence: taimen README.md; profile keys PORTHOLE_NEEDS_DTBO, PORTHOLE_DTBO_IMG
first-learned: 2026-07-24
---

Where the bootloader applies a device-tree overlay from a `dtbo` partition, the
mainline overlay and the stock one are **mutually exclusive**. Mainline needs a
stub; a stock recovery like TWRP needs the vendor one.

Two things make this expensive to diagnose:

1. **The bootloader reads dtbo from the *active slot*** — so on an A/B device
   you must flash both slots, or the next slot flip silently reverts you.
2. **Getting it wrong bounces you back to fastboot in about 3 seconds and looks
   exactly like a bad kernel.** You will debug the kernel. The kernel is fine.

Set `PORTHOLE_NEEDS_DTBO=1` and `PORTHOLE_DTBO_IMG` in the profile. When a boot
fails in ~3 s with a kernel you have no other reason to doubt, check the dtbo
before anything else — it is one command and it is usually the answer.
