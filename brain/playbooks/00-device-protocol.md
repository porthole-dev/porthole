---
id: 00-device-protocol
title: Moving a device between states without losing an hour
scope: generic
subsystem: boot
severity: technique
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md
first-learned: 2026-07-26
---

Read this once before touching a device. Everything here is already implemented
in `tools/` — **do not hand-roll any of it.** If you find yourself writing an
`ssh ... reboot` one-liner or a `sleep 60`, stop: there is a tool.

## The one reliable discriminator

`fastboot devices` prints a line **only** in the real bootloader. Not `lsusb` —
see [[usb-ids-cannot-tell-booted-from-bootloader]].

"Does ssh answer" is not "the device rebooted": the pre-reboot session keeps
answering for a second or two. Compare `/proc/sys/kernel/random/boot_id`, which
changes exactly once per boot and cannot be faked by a lingering connection.

## Into the bootloader

```sh
TK_AGENT=<you> tools/tk-device.sh tools/tk-to-fastboot.sh
```

~9 s, first try, and it does not burn a boot retry. Why the obvious command does
not work: [[busybox-reboot-eats-the-mode-string]].

## Out of the bootloader — always re-arm the slot FIRST

```sh
fastboot set_active "$PORTHOLE_ACTIVE_SLOT" && fastboot reboot   # == tk_rearm_and_boot
```

Never `set_active` a slot listed in `PORTHOLE_SLOT_FORBIDDEN`; porthole refuses.
Why a bare `fastboot reboot` drops you straight back:
[[ab-retry-counter-is-a-countdown-not-a-glitch]].

## Waiting

```sh
. tools/tk-lib.sh                 # SOURCE it, never execute it
OLD=$(tk_boot_id)
... do the thing ...
tk_wait_ssh "$OLD" "$(tk_deadline_ms 300)" && echo up
```

See [[poll-never-sleep]] and [[wait-long-enough-before-calling-a-boot-failed]].

## After any boot test

`cat /proc/version` and `cat /proc/cmdline`.
[[prove-which-kernel-answered]].

## Sharing the device

Every command that touches the device goes through the mutex, and declares the
state it needs: [[the-lock-says-who-not-what]].

## When it is genuinely wrong

[[frozen-is-not-hung]] has the four-state table and the tool for each.
Before unattended hang-risky work, read [[a-hard-hang-writes-nothing-to-disk]].
