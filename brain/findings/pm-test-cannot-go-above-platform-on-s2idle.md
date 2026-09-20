---
id: pm-test-cannot-go-above-platform-on-s2idle
title: pm_test rejects processors and core when the sleep state is s2idle
scope: generic
subsystem: power
severity: finding
confidence: proven
evidence: taimen 7.2.2 #76, 2026-09-20: pm_test freezer/devices/platform each ran the full phase and resumed (devices: every callback named and timed, slowest ftm4_resume 86399 usecs). pm_test processors and core both returned instantly with 'sh: write error: Resource temporarily unavailable' (-EAGAIN), twice each, with only 'PM: suspend entry (s2idle)' and 'PM: suspend exit' in dmesg and no Filesystems sync line -- kernel/power/suspend.c enter_state() refuses TEST_CPUS and below for PM_SUSPEND_TO_IDLE
refutes: walk the pm_test ladder up to core; a clean pm_test core run exercises syscore suspend
first-learned: 2026-09-20
---

**The question** — a resume-side hang leaves no log, so `pm_test` is the
obvious instrument: it runs the suspend and resume machinery, waits 5 s and
returns without entering the real sleep state, so `dmesg` survives. How far up
the ladder (`freezer` → `devices` → `platform` → `processors` → `core`) can it
actually be walked?

**The answer** — only to `platform`. On a system whose only `mem_sleep` is
`[s2idle]`, writing `processors` or `core` to `/sys/power/pm_test` is accepted
by the file, and the following `echo mem > /sys/power/state` then fails
immediately with `-EAGAIN`:

```
sh: write error: Resource temporarily unavailable
```

`kernel/power/suspend.c:enter_state()` refuses `pm_test_level <= TEST_CPUS`
when the state is `PM_SUSPEND_TO_IDLE`, before any freezing happens. The tell
is that `dmesg` shows `PM: suspend entry (s2idle)` followed straight by
`PM: suspend exit`, with **no** `Filesystems sync` line and no device
callbacks — nothing ran.

**What this rules out** —

- "Walk the `pm_test` ladder to `core`." Two of its five rungs do not exist
  here. `platform` is the top, and it does cover `suspend_late` /
  `suspend_noirq`, which is where `qcom_wdt` sits.
- "A clean `pm_test core` run exercises syscore suspend, so syscore is fine."
  No such run is possible; nonboot-CPU teardown and syscore are simply
  untestable this way on an s2idle-only device.
- It also does **not** support "suspend is broken": the failure is a refusal
  to test, not a failure to suspend. A real `echo freeze > /sys/power/state`
  on the same boot suspends and resumes normally.

**The trap that goes with it** — `pm_test` is sticky. It stays at whatever was
last written until something sets it back to `none`, and a left-over
`processors` makes every subsequent *real* suspend fail instantly with
`fail_before`/`fail` climbing and `last_failed_dev=` empty. That cost one
confusing `ph-suspend-cycle.sh` run on 2026-09-20 before anyone noticed the
leftover. Always `echo none > /sys/power/pm_test` when finished.

**How it was established** — taimen, kernel 7.2.2 #76, `mem_sleep` =
`[s2idle]`. `freezer`, `devices` and `platform` each ran their full phase, slept
the debug 5 s and resumed; with `pm_print_times=1` the `devices` run named and
timed every callback (slowest `ftm4_resume`, 86399 usecs). `processors` and
`core` were each tried twice, seconds apart and with a settle between, and
returned `-EAGAIN` instantly every time. It would be overturned by a kernel
that supports these levels for `PM_SUSPEND_TO_IDLE`, or by a device that also
offers a real `deep` in `mem_sleep`, where `mem` maps to `PM_SUSPEND_MEM` and
the upper rungs do run.
