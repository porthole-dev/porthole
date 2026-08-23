---
id: shipped-configuration-is-not-running-configuration
title: The shipped configuration is not the running configuration — read the value back
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: taimen AGENTS.md §3b, GPU autosuspend_delay_ms; pmaports 15f1d08b065f
first-learned: 2026-08-19
---

The setting is in the package. The file is on the device. The boot logs are
clean. The value is still wrong.

**The worked example.** The GPU's `autosuspend_delay_ms=1000` had **never
applied on any boot.** `systemd-tmpfiles-setup` runs at 16.3 s; the adreno
driver binds at 18.8 s; and `autosuspend_delay_ms_store()` returns `-EIO` until
the driver has called `pm_runtime_use_autosuspend()`. The write happened two and
a half seconds too early, every single time.

Worse, and this is the part that hides it: **a tmpfiles `w` line whose glob
matches nothing logs nothing at all.** No error, no warning, no trace that it
ran. Nothing anywhere says the setting did not take.

The fix pattern for late-binding drivers is a udev `bind` rule, which fires when
the driver is actually there. `policy*/schedutil/rate_limit_us` sits on the same
race.

**The rule, and it costs one command:**

```sh
cat /sys/.../the_value_you_set        # after boot, on the device
```

One `cat` is the whole check, and it is the one nobody runs. Any configuration
mechanism that runs at a fixed point in boot races every driver that binds
later.

Related: [[instrument-guilty-until-proven-innocent]],
[[a-systemd-dropin-cannot-remove-an-ordering-dependency]].
