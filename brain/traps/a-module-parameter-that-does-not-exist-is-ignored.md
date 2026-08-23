---
id: a-module-parameter-that-does-not-exist-is-ignored
title: A module parameter that does not exist is silently ignored
scope: generic
subsystem: kernel
severity: trap
confidence: proven
evidence: taimen AGENTS.md §3b
first-learned: 2026-08-19
---

Not an error. Not a warning. Nothing at all. The token is discarded and your
experiment reads as "hypothesis refuted".

Two of these in a single taimen session:

- `qcom_cpufreq_hw.skip_acd` / `.lut_src` existed only on a private
  instrumentation branch and never reached the packaged kernel.
- `gsi.stop_on_suspend` does not exist under that name: `gsi.o` links into
  `ipa.ko`, so the parameter is `ipa.stop_on_suspend`. Three documents had it
  wrong.

**Verify it exists before trusting any result that depends on it:**

```sh
ls /sys/module/<mod>/parameters/       # not listed = your token did nothing
grep -o '<token>[^ ]*' /proc/cmdline   # not there  = it never reached the kernel
```

That directory existing at all is also the cheapest possible proof that the
module you built is the module that loaded.

Related: [[a-null-from-an-unexecuted-path-is-not-a-refutation]],
[[prove-which-kernel-answered]].
