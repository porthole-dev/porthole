---
id: 10-first-boot
title: "Playbook: first boot"
scope: generic
subsystem: boot
severity: technique
confidence: proven
evidence: taimen docs/BUILD-RUNBOOK.md, docs/SETUP.md
first-learned: 2026-07-25
---

**Goal:** the device runs your kernel far enough to prove it, even with no
display, no storage and no network.

**Done when:** you can name the kernel that is running from evidence that is not
the screen.

## Order

1. **Find a device tree.** Someone has usually started: check
   `msm8998-mainline`-style community forks, the pmOS wiki page for your SoC,
   and any sibling device sharing it. A sibling's DTS is worth more than a
   blank page.
2. **Get a device package.** Copy the archived or existing pmaports package for
   the closest sibling device — same SoC beats same vendor. Fill in
   `deviceinfo`. **Do not guess the boot image offsets**: source them from the
   downstream mkbootimg args or an unpacked stock boot.img
   ([[dtbo-must-match-the-kernel]] for the neighbouring trap).
3. **Boot from RAM before you flash anything.** `fastboot boot <img>` ignores
   slots and leaves the installed system alone. Iterate here.
4. **Get a channel out.** In rough order of how early they work: earlycon on a
   UART if one is exposed, then the initramfs debug shell over USB, then the
   pmOS USB gadget with ssh. Do not proceed without one — see
   [[a-hard-hang-writes-nothing-to-disk]].
5. **Classify the outcome mechanically.** `tools/boot-probe.sh` reports
   BOOTED / REBOOTED / HUNG / REJECTED from USB state with a confirmed-disconnect
   gate. Judging by eye is [[never-judge-a-boot-by-the-screen]].

## The traps that cost the most here

- [[never-judge-a-boot-by-the-screen]]
- [[usb-ids-cannot-tell-booted-from-bootloader]]
- [[dtbo-must-match-the-kernel]] — a ~3 s bounce that looks like a bad kernel
- [[wait-long-enough-before-calling-a-boot-failed]]
- [[olddefconfig-silently-drops-symbols]]

## Then

Record what you learned in `profiles/<codename>/device.env` immediately. The
boot-retry count, the forbidden slot, whether lsusb lies — each of those is a
fact you will otherwise re-derive at 3am.
