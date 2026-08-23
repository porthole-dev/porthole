---
id: 40-suspend
title: "Playbook: suspend and deep sleep"
scope: generic
subsystem: power
severity: technique
confidence: proven
evidence: taimen docs/BLUEPRINT/BP-01-suspend-deep-sleep.md, BP-14-suspend-endgame.md
first-learned: 2026-08-19
---

**Goal:** the device enters real system sleep and wakes reliably.

**Done when:** repeated cycles wake cleanly, *and* you can show the SoC actually
reached the low-power state rather than idling in s2idle.

## This is where [[every-test-needs-a-positive-control]] earns its keep

Suspend work generates more false nulls than any other subsystem, because almost
everything about it is invisible from userspace and "nothing happened" is
indistinguishable from "it worked".

Decide, before the run, which number proves the path executed. A counter that
must increase. A residency that must climb. The worked example that started that
law was a suspend arm: `runtime_suspended_time` climbing looked like success,
and `runtime_active_time` never moving proved the callback under test had not
run once.

## Order

1. **Get s2idle working first**, then chase deeper states. They are different
   problems and mixing them wastes both.
2. **Find what votes against sleep.** Wakeup sources, runtime-PM refcounts held
   by drivers that never idle, clocks nobody released.
3. **Cycle in bulk, not once.** `tools/tk-suspend-cycle.sh`. A suspend bug that
   appears one time in twenty is invisible to a single-cycle test and will
   define your daily-driver experience.
4. **Long sleeps are a separate test.** A device that survives twenty 10-second
   cycles can still die on one 30-minute sleep — different clocks, different
   rails, different timeouts.

## The traps

- [[watchdog-out-of-range-disarms-instead-of-clamping]] — and note the watchdog
  never fires on [[frozen-is-not-hung]]
- [[a-hard-hang-writes-nothing-to-disk]] — arrange evidence before you trigger
- [[shipped-configuration-is-not-running-configuration]] — every power tunable
  set at a fixed point in boot races the driver it targets

## Tooling

A tool that deliberately induces a reset must treat "the device stopped
answering" as the **expected** outcome, and must therefore put a timeout on
every ssh. `tk-suspend-cycle.sh` does this; an ad-hoc `ssh ...; ssh ...`
one-liner does not, and one held the device lock for ten minutes against every
other agent that day.
