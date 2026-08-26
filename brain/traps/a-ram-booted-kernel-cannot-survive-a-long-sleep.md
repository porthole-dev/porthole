---
id: a-ram-booted-kernel-cannot-survive-a-long-sleep
title: A RAM-booted kernel cannot survive a long sleep — suspend work needs a flashed slot
scope: generic
subsystem: power
severity: trap
confidence: proven
evidence: taimen, 2026-08-23 — eight watchdog resets before the pattern was named; short cycles passed on the same image that died on every long one
first-learned: 2026-08-23
---

`fastboot boot` is the safety net every bring-up leans on, and
`porthole build boot` makes it the cheapest rung on the build ladder: nothing is
written to flash, a bad kernel costs one power cycle. That is exactly right for
iterating on a driver or a DTS.

**It is wrong for validating suspend.** A RAM-booted kernel survives short
s2idle cycles and dies on long ones.

The deep RPM / XO-shutdown state only develops minutes into a sleep, and waking
out of it needs bootloader warm-boot setup that a flashed-slot boot gets and a
RAM boot does not. So:

- suspend cycles of about a minute or less: pass on a RAM boot
- anything longer: watchdog reset at the s2idle exit, leaving a driverless
  crumb at phase `timekeeping_freeze END` and nothing else

That crumb names no driver, so the natural reading is "some driver broke
suspend" — and the next hours go into bisecting drivers that were never the
problem. On taimen it took eight resets before anyone questioned the *boot
method* rather than the kernel.

## The rule

**Suspend or wake validation beyond ~1 minute of sleep MUST run on a flashed
slot.** Flash one slot, keep the known-good kernel on the other, and RAM-boot
only for the work RAM-booting is good at.

```sh
porthole build boot --yes     # driver/DTS iteration -- fine
porthole build fast --yes     # then flash, before any long-sleep test
```

## Why this is not "just taimen"

The mechanism is a bootloader warm-boot handoff, not a Qualcomm quirk, so treat
it as the default assumption on any device with an A/B bootloader until you have
evidence otherwise. It is cheap to respect and expensive to rediscover.

The related failure — a RAM boot that ignores the ramdisk entirely — is
[[fastboot-boot-ignores-the-ramdisk-on-newer-pixels]]. Both share a moral: a RAM
boot is not a faithful simulation of a flashed boot, and the differences surface
in whichever subsystem you are least expecting.

Related: [[unmasked-suspend-during-an-automated-wait-is-a-death-loop]],
[[prove-which-kernel-answered]], [[wait-long-enough-before-calling-a-boot-failed]].
