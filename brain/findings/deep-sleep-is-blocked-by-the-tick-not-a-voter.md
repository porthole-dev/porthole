---
id: deep-sleep-is-blocked-by-the-tick-not-a-voter
title: Deep sleep is blocked by a 5.3k/s tick inside the frozen window, not by a missing RPM voter
scope: device:google-taimen
subsystem: power
severity: finding
confidence: proven
evidence: taimen 7.2.2, 2026-09-20. state3 system-pc ships disable=1; enabling it for one cycle and reading the RIGHT counter (state3/s2idle/usage, not state3/usage -- enter_s2idle_proper() increments s2idle_usage only) shows it IS entered, ~1.38M times per CPU with ~49.8s residency, while state2/s2idle/usage stays 0. vmin/vlow still read Count: 0. /proc/interrupts delta across a 120 s sleep: 660852 arch_timer, 237 glink-rpm, 1712 IPI1. events/timer/hrtimer_expire_entry over a 30 s sleep: 163158 of 163239 events are tick_nohz_handler on <idle>. events/irq/irq_handler_entry counted ONLY between machine_suspend begin and end: 160056 arch_timer and 28 IPI over 30 s, i.e. ~5335/s against CONFIG_HZ=1000 with 8 CPUs. CONFIG_NO_HZ_IDLE=y
refutes: mainline msm8998 has no RPM sleep-set handshake so deep sleep cannot be reached; the system power collapse idle state is never entered; only 8 IPIs and 2 arch_timer fire inside the machine_suspend window; vmin/vlow are 0 because some master has not voted sleep
first-learned: 2026-09-20
---

**The question** — `qcom_stats` has always read `vmin Count: 0` and
`vlow Count: 0`, and the standing explanation was that mainline msm8998 has no
RPM sleep-set handshake at all, so deep sleep "cannot be fixed here". Is that
true?

**The answer** — no. The handshake is there and the AP does reach system power
collapse. What stops RPM is that the AP will not stay down: the timer tick fires
about **5,300 times a second inside the frozen suspend window**, so RPM never
gets a quiet interval in which to swap to its sleep sets.

**Three measurements, each replacing a wrong one** —

1. **`system-pc` is entered.** It ships `disable=1`; enable it for one cycle and
   the counter to read is `state3/s2idle/usage`, **not** `state3/usage` --
   `enter_s2idle_proper()` increments `s2idle_usage` and `s2idle_time` only, so
   `usage` stays 0 no matter how many times s2idle enters the state. Read the
   right one and it is ~1.38M entries per CPU with ~49.8 s of residency, while
   `state2/s2idle/usage` is 0. Reading `usage` is what produced the earlier
   "never entered" conclusion.

2. **The wakes are the tick.** `/proc/interrupts` differenced across a 120 s
   sleep: `660852 arch_timer`, `237 glink-rpm`, `1712 IPI1`, everything else in
   the tens. `events/timer/hrtimer_expire_entry` over a 30 s sleep:
   **163158 of 163239 events are `tick_nohz_handler`, on `<idle>`.**

3. **They are inside the frozen window, not loop churn around it.** Counting
   `events/irq/irq_handler_entry` only between `machine_suspend` begin and end:

   ```
    160056 arch_timer
        28 IPI
   ```

   Over 30 s, against `CONFIG_HZ=1000` on 8 CPUs. This directly refutes the
   "8 IPIs and 2 arch_timer inside the window" figure recorded earlier.

**The mechanism** — the idle loop's s2idle branch `goto exit_idle`s past
`tick_nohz_idle_stop_tick()`, so nothing puts the CPU into dynticks-idle; s2idle
relies on `tick_freeze()` instead, and `tick_freeze()` only suspends timekeeping
once **all** online CPUs are in it simultaneously. With eight CPUs and a 1 ms
tick they do not converge: each `tick_unfreeze()` leaves a tick armed that
becomes the next wake, which is self-sustaining at roughly HZ.

**What this rules out** —

- "No RPM sleep-set handshake exists, so this is separate work." The state, the
  DT, and the handshake are in the tree (patches 0144, 0148, 0150, 0151).
- "The idle state is never entered." It is, with real residency.
- "Some master has not voted sleep." Possible but unproven and not the first
  problem: the AP's own residency is ~36 us average against a 25 ms
  min-residency, so nothing downstream ever gets a chance.

**Where to go next** — make the tick stop rather than be re-armed: fewer online
CPUs across suspend, a lower HZ, or getting the s2idle path to stop the tick.
Do not chase RPM voters until the AP stays down longer than `system-pc`'s
25 ms min-residency. And note that `system-pc` is deliberately
`idle-state-disabled-by-default` because XO shutdown stops the QTIMER, so only
an MPM pin (PMIC PON, RTC alarm via SPMI pin 87) can wake it -- it is meant to
be enabled around s2idle by a hook, which does not exist yet.
