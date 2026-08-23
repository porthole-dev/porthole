---
id: a-null-from-an-unexecuted-path-is-not-a-refutation
title: A null from a path that never executed is not a refutation
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: taimen AGENTS.md §3b; the qcom_cpufreq_hw.skip_acd and gsi.stop_on_suspend arms
first-learned: 2026-08-19
---

Crossing a hypothesis off the list is a decision with a cost: you will not
return to it. Make sure you are crossing off a *tested* hypothesis.

Two arms in one taimen session were refuted by nothing at all:

- `qcom_cpufreq_hw.skip_acd` and `.lut_src` existed only on a private
  instrumentation branch and never entered the aport series. Passed to the
  packaged kernel, the token is simply discarded. The arm read as "hypothesis
  refuted".
- `gsi.stop_on_suspend` does not exist under that name at all: `gsi.o` links
  into `ipa.ko`, so the parameter is `ipa.stop_on_suspend`. **Three documents
  had it wrong**, and following any of them produces a confident false negative.

Neither produced an error. Neither produced a warning.

Before you record a negative result, answer: *what would be different if the
code under test had never run?* If the answer is "nothing", you have not run an
experiment.

Related: [[a-module-parameter-that-does-not-exist-is-ignored]],
[[every-test-needs-a-positive-control]],
[[instrument-guilty-until-proven-innocent]].
