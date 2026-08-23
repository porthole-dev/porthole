---
id: every-test-needs-a-positive-control
title: Decide which of your numbers is the control before you run the arm
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: taimen AGENTS.md §3b, the IPA idle-observation arm
first-learned: 2026-08-19
---

Every measurement needs a second number whose job is to prove the first one
means anything.

**The worked example.** An IPA idle-observation arm watched
`power/runtime_suspended_time` climb. It looked clean and would have been
written up as a result. But `power/runtime_active_time` never moved, which meant
the callback under test had not run once. `susp_ms` was the reading; `act_ms`
was the control, and only the control was informative.

**Decide which of your numbers is `act_ms` before you run the arm.** Afterwards
you will rationalise whichever number you happen to have.

Cheap controls, in rough order of how often they save you:

- a counter that must increase if the code ran at all
- the value read back from the device, not the value you wrote
- a deliberately-broken arm that must fail (if it passes, your test is inert)
- the total your filter matched before you count matches within it — see
  [[a-journal-grep-matches-your-own-command-line]]
- `ls /sys/module/<mod>/parameters/` before trusting anything a module
  parameter was supposed to change

Related: [[instrument-guilty-until-proven-innocent]].
