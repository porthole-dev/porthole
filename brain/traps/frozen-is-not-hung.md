---
id: frozen-is-not-hung
title: FROZEN (kernel alive, userspace gone) is a distinct state and the watchdog will not save you
scope: generic
subsystem: diagnosis
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §6; porthole tk_device_state
first-learned: 2026-08-19
---

Four states, and treating them as two costs hours:

| state | how it looks | what to do |
|---|---|---|
| BOOTED | ssh answers | — |
| FROZEN | ping answers, ssh does not | rescue shell; `ph-recover.sh` |
| FASTBOOT | `fastboot devices` prints a line | `set_active` + `fastboot reboot` |
| ABSENT | nothing on USB at all | **needs a human**: long-press power |

**FROZEN is the one that surprises people.** The kernel is alive and petting the
watchdog happily, so the watchdog will *never* fire on it. On taimen the
recurring cause was an sshd/PAM stall — sshd taking ~7.9 s to answer a trivial
command, or not answering at all — with everything else healthy.

That same stall is what makes [[empty-must-mean-unknown-never-changed]] matter:
a probe timeout against a FROZEN device returns empty, and empty must not be
read as "rebooted".

`tk_device_state` distinguishes all four in ~0.4 s cold, under 0.1 s with a warm
ssh master. Use it rather than inferring state from one failed command.

Related: [[the-lock-says-who-not-what]], [[usb-ids-cannot-tell-booted-from-bootloader]].
