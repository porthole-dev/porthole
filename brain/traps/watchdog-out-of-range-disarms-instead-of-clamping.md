---
id: watchdog-out-of-range-disarms-instead-of-clamping
title: An out-of-range watchdog timeout turns the watchdog OFF, it does not clamp
scope: generic
subsystem: power
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §6, AGENTS.md §3.2; device-google-taimen r8
first-learned: 2026-08-19
---

`qcom,kpss-wdt` on msm8998 has `max_tick_count = 0xFFFFF` over a 32764 Hz sleep
clock, so its `max_timeout` is **32 s**.

A drop-in carried `RuntimeWatchdogSec=60` for a day. `WDIOC_SETTIMEOUT(60)`
returns `EINVAL`, and **systemd's response is to disarm the watchdog and close
`/dev/watchdog`** — announced in exactly one journal line:

```
Failed to set watchdog hardware timeout to 1min: Invalid argument
```

So the drop-in whose entire purpose was to arm the watchdog was switching it
off. Never configure above the hardware ceiling; record it as
`PORTHOLE_WATCHDOG_MAX_S`.

**The part worth carrying to every device:
`/sys/class/watchdog/watchdog0/timeout` read `30` the whole time the watchdog
was disarmed.** Only `state` discriminates. Anyone checking `timeout` alone
calls a dead watchdog healthy.

The stronger control is ownership, not configuration:

```sh
cat /sys/class/watchdog/watchdog0/state     # must say: active
ls -l /proc/1/fd | grep watchdog            # pid 1 must hold the device
```

If pid 1 does not hold an fd on it, systemd is not the one petting it.

**And know what it does not cover.** A watchdog catches hard hangs. It will
never fire on a userspace stall with the kernel alive and petting happily —
which on taimen was the recurring FROZEN state.

Related: [[shipped-configuration-is-not-running-configuration]],
[[every-test-needs-a-positive-control]].
