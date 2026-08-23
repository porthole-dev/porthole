---
id: handoff-format
title: The handoff document
scope: generic
subsystem: method
severity: technique
confidence: proven
evidence: taimen docs/HANDOFF-*.md, 20 of them
first-learned: 2026-08-02
---

A bring-up is a relay. Sessions end mid-experiment, context is lost, and the
next reader — often an agent with no memory of any of it — needs to pick up
without re-deriving.

A handoff that works has five parts:

1. **State right now.** What is flashed, what is running, what physical state
   the device is in. First, because it is what a wrong assumption costs most.
2. **What was proven this session.** Observations with evidence. Include the
   refutations.
3. **What was NOT proven.** Explicitly. This is the part everyone skips and the
   part that saves the most time: it stops the next session re-running an
   inconclusive experiment and reaching the same non-conclusion.
4. **The next concrete step.** Not "investigate suspend" — the actual command,
   with the actual arm, and what result would mean what.
5. **Anything currently unsafe.** A half-flashed slot, a disarmed watchdog, an
   experiment someone else has running.

**Supersede rather than accumulate.** Twenty handoffs where nineteen are stale
is worse than one that is current, because a reader cannot tell which is which.
Name the file so the newest is obvious, and say in it which document it replaces.

Related: [[evidence-discipline]], [[agent-protocol]].
