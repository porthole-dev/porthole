---
id: the-skin-ladder-caps-the-die-22c-and-is-self-limiting
title: The vendor's skin thermal ladder drops the die 22 C under sustained load, and its own first two rungs stop it reaching the rest
scope: device:google-taimen
subsystem: thermal
severity: finding
confidence: proven
evidence: "taimen, kernel 7.2.2 #59, aport r59, cabled, screen on, on a table. 480 s `stress-ng --cpu 8` logged every 2 s to /var/log/tk-skin-burn.csv by tools/tk-skin-burn.sh (240 burn samples + 12 cooldown). Rung engagements at t=16 s skin 38.12 C -> policy4 scaling_max_freq 1804800, t=88 s skin 39.95 C -> 1497600. Peak skin 42.56 C, peak die 52.8 C. policy4 residency 2361600=16 s, 1804800=72 s, 1497600=392 s. Baseline for comparison is the pre-ladder 180 s burn recorded in taimen/docs/ANALYSIS-2026-09-19-POWER-AND-THERMAL.md §2: die 73.5-75.1 C, cpu4 cooling state 0-2 of 29, big cluster 2342400-2361600. All 11 state->frequency mappings separately verified by writing cur_state directly and reading back scaling_max_freq / devfreq max_freq."
refutes: "mainline msm8998 cannot express the vendor's thermal policy without a new driver; the tsens junction zones are the place to fix taimen's heat; the a540 GPU and the little cluster need capping before the phone runs cool; a long thermal ramp is needed to reach the upper rungs of the skin ladder"
first-learned: 2026-09-19
---

**The question** — taimen parks at 73.5-75.1 C junction under a CPU burn with
essentially no mitigation, one degree under a trip that then walks the big
cluster from 2.36 GHz to 300 MHz. The vendor never gets near that, and drives
everything from a **skin** thermistor instead. Can mainline express the
vendor's policy, and if it can, what does it actually buy?

**The answer** — it can, entirely in devicetree, and it is worth **22 C** of
die temperature.

The whole policy is six passive trips on a `skin-thermal` zone fed by
`pm8998_adc_tm` channel 4, with `cooling-maps` bounded `min == max`:

| skin | cpu4 state | cpu0 state | gpu state |
|---|---|---|---|
| 38 C | 9 (1804800) | — | — |
| 40 C | 13 (1497600) | — | — |
| 45 C | 17 (1190400) | — | — |
| 48 C | 17 (1190400) | 10 (1094400) | 4 (414 MHz) |
| 50 C | 21 (902400) | 13 (883200) | 5 (342 MHz) |
| 52 C | 29 (300000) | 21 (300000) | 6 (257 MHz) |

Bounding each map `min == max` is the load-bearing detail. The vendor's
`SKIN-MONITOR2` is a discrete threshold table (`algo_type monitor`), not a
gradient; an unbounded `THERMAL_NO_LIMIT` map would make `step_wise` walk one
state per sample towards the cap, which is a different controller wearing the
same numbers. With `lower == upper == N`, `get_target_state()` clamps to N on
the first evaluation, for both the rising and falling branches.

Measured, 480 s `stress-ng --cpu 8`:

```
t=  0s  skin 37.13 C  die 40.8 C  cd1=0   policy4 2361600
t= 16s  skin 38.12 C  die 47.9 C  cd1=9   policy4 1804800
t= 88s  skin 39.95 C  die 50.4 C  cd1=13  policy4 1497600
peak    skin 42.56 C  die 52.8 C
```

against **73.5-75.1 C die** for the pre-ladder 180 s burn. The new run is
**2.7x longer** and still 22 C cooler, so the comparison is conservative.

**The second half is the surprise: the ladder is self-limiting.** Rungs 45, 48,
50 and 52 were never reached, and no ramp will reach them on a table. Capping
the big cluster at 1497600 removes the heat that would have driven skin higher,
so it plateaus at ~42.5 C — three degrees below the next rung. `policy4` spent
392 s of 480 at 1497600 and the curve was flat, not still climbing. That means:

- the upper four rungs cannot be validated by heat on a table, only by writing
  `cur_state` directly (done: all 11 state->frequency mappings check out), or
  by an insulated arm, which needs a human present;
- the bistable failure mode is gone. Before, the phone ran full tilt until a
  75 C cliff took the cluster to 300 MHz; now it settles at a sustainable
  1.5 GHz big / 1.9 GHz little and stays there.

**What this rules out** — that any of this needed a driver. `qcom-spmi-adc-tm5`
already binds `qcom,spmi-adc-tm-hc`, the ADC5 channel map already has
`ADC5_AMUX_THM5_100K_PU` at 0x51 with the right scaling, and `adc-tm@3400` was
already in `pm8998.dtsi` behind `status = "disabled"`. It also rules out the
junction zones as the place to work: the die never came near its 75 C trip once
skin was driving, so tuning tsens trips would have changed nothing. And it
rules out the GPU and the little cluster as part of the fix — neither is capped
before 48 C by the vendor, and neither needed to be.

**How it was established** — two independent measurements, deliberately split
so a null in one is readable. (1) A static map check with the thermal core idle:
write each vendor state index to `cooling_deviceN/cur_state`, read back
`scaling_max_freq` / devfreq `max_freq`, restore to 0. All 11 exact. This
proves the *targets* without any heat. (2) The burn above, which proves the
*trips fire in order* and what that costs. Sampling was every 2 s, matching the
vendor's own `sampling 2000`, which is also `polling-delay-passive`.

Two caveats to carry forward. **USBIN is not comparable across the two arms** —
the burn median was 921 mA here against 847 mA in the baseline, but that
instrument includes charging current and the two runs were on different days at
different states of charge, so nothing about power can be claimed from it; only
the die and skin numbers are sound. And **the hysteresis release is slower than
it looks**: 60 s of cooldown did not drop cd1 below 13, because skin was still
above 39.0 C, which is the 40 C trip minus the vendor's 1 C. That is correct
behaviour, not a stuck cooling device, and the earlier idle observation caught
the full engage/release cycle on the 38 C rung.

It would be overturned by an insulated or ambient-warm arm reaching 45 C skin
and the third rung failing to engage — the upper four rungs have verified
targets but no observed trip crossing. See
[[a540-lm-is-mostly-vendor-parity]] for what is and is not still open on the
GPU side.
