---
id: wait-long-enough-before-calling-a-boot-failed
title: Wait long enough before calling a boot failed
scope: generic
subsystem: boot
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §3
first-learned: 2026-07-26
---

On taimen, sshd does not listen until about **111 s** after power-on. Verdicts
were called wrongly at 75–90 s more than once — each one a working kernel
recorded as a failure.

**Wait at least 180 s before calling a boot failed**, and measure your own
device's number rather than inheriting this one. Record it as
`PORTHOLE_REBOOT_BUDGET_S` in the profile.

The number is not the point. The point is that "how long until userspace
answers" is a *measurement you must take once*, and until you have taken it
every boot verdict you issue is a guess. Take it on a known-good kernel, on a
cold boot, and write it down.

Note this is a different quantity from a warm reboot cycle, which on taimen was
~30 s forced and ~45 s graceful.

Related: [[poll-never-sleep]], [[prove-which-kernel-answered]].
