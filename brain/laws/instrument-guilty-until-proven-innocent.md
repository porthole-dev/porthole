---
id: instrument-guilty-until-proven-innocent
title: The instrument is guilty until proven innocent
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: taimen AGENTS.md §3b; taimen docs/HANDOFF-2026-08-19-CPUFREQ-SUSPEND.md §3
first-learned: 2026-08-19
---

**The largest time sink in a bring-up is not bugs. It is an experiment that ran,
produced a clean-looking null, and never exercised the code under test.**

The shape is always the same: nothing errors, nothing logs, and the null reads
as a refuted hypothesis. You cross a theory off the list and move on. The theory
was never tested.

A single session on taimen produced a whole page of these. Each one cost the
time to design the experiment, run it, and reason about a result that did not
exist.

So: before you believe a negative result, prove the path executed. Not the
program — the *path*. A test that ran to completion and touched none of the code
under test is indistinguishable, from the outside, from a test that ran and
found nothing.

Corollaries with their own notes: [[every-test-needs-a-positive-control]],
[[a-module-parameter-that-does-not-exist-is-ignored]],
[[shipped-configuration-is-not-running-configuration]].

This is the most portable thing in this brain. It is not about phones.
