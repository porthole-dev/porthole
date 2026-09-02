---
id: the-msm8998-thermal-trip-is-a-cliff
title: The mainline msm8998 thermal zone is a cliff -- one passive trip, no limit, 2.36 GHz to 500 MHz in about 7 s
scope: soc:msm8998
subsystem: power
severity: finding
confidence: proven
evidence: "taimen 2026-09-02, kernel 7.2.2. arch/arm64/boot/dts/qcom/msm8998.dtsi cpu4-thermal has one passive trip at 75000 with hysteresis 2000, critical at 110000, polling-delay-passive 250, and cooling-map bounds THERMAL_NO_LIMIT/THERMAL_NO_LIMIT. Measured under sustained video: cooling_device cpufreq-cpu4 cur=29 max=29 and cpufreq-cpu0 cur=21 max=21, scaling_max_freq 576000 and 499200 at 74-75 C. Recovery from a paused load: 40 s to 57.4 C, cool state 0, cap back to 2361600."
refutes: "the browser stutter is thermal throttling"
first-learned: 2026-09-02
---

**The question** — under sustained load the big cluster reads 499-576 MHz and
stays there. Is the thermal policy sane?

**The answer** — no. `cpu4-thermal` in the shared SoC dtsi has exactly one
passive trip and lets `step_wise` walk the cluster to its **lowest** OPP:

```dts
cpu4_alert0: trip-point0 { temperature = <75000>; hysteresis = <2000>; type = "passive"; };
cpu4_crit:   cpu-crit    { temperature = <110000>; type = "critical"; };
cooling-maps { map0 { trip = <&cpu4_alert0>;
    cooling-device = <&cpu4 THERMAL_NO_LIMIT THERMAL_NO_LIMIT>, ... }; };
```

`polling-delay-passive = <250>` and a 29-state cooling device means **about
7 seconds from 2.36 GHz to the floor**, with nothing between 75 C and critical
at 110 C. Both clusters were observed pinned at maximum cooling state
(`cpufreq-cpu4 cur=29 max=29`, `cpufreq-cpu0 cur=21 max=21`) while the sensor
read 73.8-75 C -- i.e. hovering at the trip, so it never sustainably steps back
down while the load continues.

This is in `msm8998.dtsi`, not a device dts, so it applies to every msm8998
port (walleye, taimen, fxtec pro1, the clamshells).

**It is NOT the browser stutter.** Measured directly, and this is the useful
half of the note: the *coolest* arm stalled worst -- 67.7 C, cooling state 0,
GPU at its 257 MHz idle rate, and a 1221 ms stall -- while an arm pinned at
1.03 GHz with the governor actively stepping (74.8 C, state 3->6) had **zero**
stalls over 100 ms. See
[[the-browser-stutter-is-a-blocked-webkit-main-thread]] before blaming heat for
a frame-timing symptom.

What the cliff does cost is everything that wants sustained CPU: the cluster
sits at a fifth of its clock within seconds of any real workload, and recovery
takes ~40 s of idle.

**What would fix it** — a trip gradient rather than one wall, and bounded
cooling states so the governor cannot floor the cluster from a single trip.
Upstreamable, since it is the SoC dtsi. Before sending anything, check the
value against what the vendor DT actually used; 75 C is conservative for this
silicon and the fix is as much about `THERMAL_NO_LIMIT` and the missing
intermediate trips as about the number.
