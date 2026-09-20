---
id: disabling-cpu-retention-is-parity-theatre
title: Disabling the CPU retention idle state, as the vendor does, is worth about 0.02 mW and does not deepen idle
scope: soc:msm8998
subsystem: power
severity: finding
confidence: proven
evidence: taimen 7.2.2 #73, blanked, full phosh session, two interleaved A/B rounds of 60 s, cpuidle residency summed over all 8 CPUs: retention enabled -> WFI 0.07/0.09 %, retention 0.43/0.48 %, PC 99.50/99.43 %; disabled -> WFI 0.59/0.56 %, retention 0, PC 99.42/99.44 %. Vendor ss-power from msm8998-pm.dtsi: silver WFI 454 mW, retention 449 mW, PC 436 mW.
refutes: the vendor disables CPU retention because it pushes idle into power collapse; gap 8 (retention idle states enabled) is a real parity gap worth closing; msm8998 idle is not reaching power collapse
first-learned: 2026-09-20
---

**The question** — the 2026-09-19 vendor teardown listed "retention idle
states enabled" as parity gap 8: `init.taimen.rc:98-109` writes
`idle_enabled N` to every CPU `ret` level and to both clusters' `l2-dynret`
and `l2-ret`, so the shipping phone goes WFI -> power collapse with nothing in
between. Ours has the middle rung on. It is one sysfs write per CPU and it was
called "directly vendor parity". Is it worth anything?

**The answer** — **about 0.02 mW, and it does not do what the gap assumed.**
Our `state1` is that rung (`cpu-sleep-0-0` / `cpu-sleep-1-0`, psci param 0x2).
Disabling it on all eight CPUs moves idle time out of retention and into
**WFI**, not into power collapse:

| | WFI | retention | power collapse |
|---|---|---|---|
| enabled (stock), 2 rounds | 0.07 % / 0.09 % | **0.43 % / 0.48 %** | 99.50 % / 99.43 % |
| disabled (vendor), 2 rounds | 0.59 % / 0.56 % | 0 | 99.42 % / 99.44 % |

The power-collapse share does not move. Against the vendor's own `ss-power`
figures (`msm8998-pm.dtsi`: silver WFI 454 mW, retention 449 mW, PC 436 mW),
shifting 0.47 % of idle time from 449 mW to 454 mW is 0.024 mW. There is no
mechanism here, in either direction.

**The useful fact from the same measurement**: this phone already spends
**99.4 % of idle CPU-time in power collapse**. Per-CPU collapse is healthy and
has been all along; what is missing is the *system* level, which is the RPM
handshake -- and that is a firmware wall, not a cpuidle one.

**What this rules out** —

- *"The vendor disables retention because it deepens idle."* It does not. The
  governor spends the freed time in WFI.
- *"Gap 8 is worth closing."* It is worth 0.02 mW. Shipping it would be parity
  with no measurable effect, and it was dropped from
  `device-google-taimen`'s `taimen-perf.conf` for exactly that reason.
- *"msm8998 idle is not reaching power collapse."* 99.4 %.

**How it was established** — `cpuidle/state*/time` summed across all 8 CPUs,
two interleaved 60 s A/B rounds with a 3 s settle after each toggle, screen
blanked, full phosh session, kernel 7.2.2 #73. Both rounds agree to within a
tenth of a percent. Notably this needs NO current meter: the USBIN instrument
has a ~4.6 mA floor and could never have resolved this
([[the-camera-hold-was-the-whole-244mw]]), whereas residency times the
vendor's published per-state power answers it outright.

Overturned by: a workload with a very different idle-duration distribution --
these shares are for an idle, blanked phone, and a busy one has shorter idles
where the middle rung could matter more.
