---
id: a-check-that-fires-on-a-healthy-tree-gets-muted
title: A check that fires on a healthy tree gets muted
scope: generic
subsystem: tooling
severity: law
confidence: proven
evidence: 2026-09-10. docs/DESIGN-fork-provenance-and-host-sync.md section 9 specified `porthole sync` asserting each repo sits on the branch the profile names. Measured the three repos before implementing: pmaports was on `perf/crossdirect-native-link`, and on a re-probe forty minutes later on `taimen-bringup` -- neither the `edge` its own pmaports.cfg declares nor the `taimen-bringup` that porthole_cmd_channel.py's comments assert. Both are healthy states. Shipped as a report beside the actual branch, exit code untouched.
first-learned: 2026-09-10
---

**Measure what the normal state actually is before you write the assertion. If
the check would fire on a tree that is fine, it is not a check -- it is noise
with an exit code, and the exit code is what gets deleted first.**

The tempting move is to promote a documented expectation into an enforced one.
`PORTHOLE_KERNEL_BRANCH` had rotted for a day precisely *because* nothing read
it, and `doctor`'s own comment draws the right conclusion: "Nothing reads
KERNEL_BRANCH -- it is documentation, which is exactly why it rotted." So the
obvious fix is to make something read it and fail on a mismatch.

That conclusion is right about the *cause* and wrong about the *remedy*, and
which one you get depends entirely on a measurement nobody takes: **how often
is the expected value not the actual value on a healthy host?**

For `pmaports` the answer was "most of the time". Sitting on a feature branch
is what working on a fork looks like. An assertion there would have failed on
the reference host on the day it shipped, and the second or third time someone
saw it fire on a tree they knew was fine, the check stops being read at all --
`|| true`, an entry in a skip list, or just an eye that slides past the line.
That is strictly worse than the unread key it replaced: the unread key was
merely inert, while the muted check trains you to ignore a whole class of
output.

The distinction that survives:

- **Enforce** what is wrong *whenever* it differs — a fork upstream has
  outranked, a serial in a tracked file, a boot image the rootfs cannot
  satisfy. The mismatch IS the defect.
- **Report** what merely *often* differs — which branch a repo is on, which
  host a path came from. The mismatch is information, and the reader supplies
  the judgement.

The test for which one you have is not how strongly the rule is felt. It is a
measurement of the healthy state, taken before the code is written. See
[[an-unenforced-rule-is-a-defect-not-documentation]] for the opposite failure,
and note that the two are not in tension: a rule worth enforcing must first be
one that a healthy tree passes.
