---
id: the-vendor-runs-four-thermal-layers
title: The vendor runs four thermal layers on msm8998; mainline runs one, and two of the other three are hardware we switch off
scope: soc:msm8998
subsystem: power
severity: finding
confidence: proven
evidence: Google wahoo kernel android-msm-wahoo-4.4-qt-qpr3 (tag android-10.0.0_r0.71). Layer 1: drivers/thermal/msm_lmh_dcvs.c:444-446 enables a TZ algorithm over SCM per cluster, ARM threshold 65000 / HIGH 95000 (:60-62), DT qcom,limits-dcvs@0/@1 msm8998.dtsi:3365-3374, GIC SPI 37/38; mainline drivers/thermal/qcom/lmh.c binds only sdm845/sm8150/sc8180x. Layer 2: msm8998-gpu.dtsi qcom,lm-limit=6000 + base-leakage-coefficient + gpu-efuse-leakage, vs mainline a5xx_power.c:175-207 a540_lm_setup() which sets AGC_LM_CONFIG_THROTTLE_DISABLE and AGC_LM_CONFIG_BCL_DISABLED and pins the GPMU to level 0 / fast_rate. Layers 3-4: thermal-engine.conf and thermal_info_config.json both drive everything from bd_therm2 SKIN 38/45/48/50/52/54 C while tsens junction only reaches SEVERE at 95 C. Measured on taimen 2026-09-19 kernel 7.2.2: 180 s CPU burn held 73.5-75.1 C with cooling state 0 in 71 of 84 samples, max state 2 of 29.
refutes: the 75 C trip cliff is the whole heat story; mainline just needs more trip points; the GPU has no hardware power limiter; msm8998 thermal policy is junction-based
first-learned: 2026-09-19
---

**The question** — the SoC gets hot under load and then falls off a cliff. Our
thermal zone has one passive trip at 75 C with `THERMAL_NO_LIMIT` bounds; is
adding trip points the fix?

**The answer** — no, that is one layer of four. The vendor defends this die in
four places, and two of the missing three are **hardware we explicitly switch
off in our own driver**.

| # | layer | lives in | trigger | ours |
|---|---|---|---|---|
| 1 | LMH DCVS | **TrustZone**, per cluster | **65 C junction** (arm), 95 C high | absent |
| 2 | GPMU Limits Mgmt | **on the GPU die** | current/leakage budget | **built, DISABLED** |
| 3 | msm_thermal + thermal-engine | kernel + userspace | **38 C SKIN** | absent (no skin sensor) |
| 4 | Android Thermal HAL 2.0 | userspace | **38 C SKIN** | n/a |

**Layer 1 — TZ's limiter is never armed.** `msm_lmh_dcvs.c:444-446` makes one
SCM call at probe (`MSM_LIMITS_SUB_FN_THERMAL` / `ALGO_MODE_ENABLE`, per
cluster `0x6370302D`/`0x6370312D`); TZ then caps the OSM autonomously and Linux
only reads the cap back from `0x179C1B04`/`0x179C3B04`. Mainline's
`drivers/thermal/qcom/lmh.c` binds **only** `qcom,sdm845-lmh`, `qcom,sm8150-lmh`
and `qcom,sc8180x-lmh` — the newer LMH v2. msm8998's `qcom,msm-hw-limits` SCM
interface has no mainline driver, so the enable call is never made.

**Layer 2 — we tell the A540's own power manager to stand down.**
`a5xx_power.c:175-207`, `a540_lm_setup()`, sets `AGC_LM_CONFIG_THROTTLE_DISABLE`
("For now disable GPMU side throttling") and `AGC_LM_CONFIG_BCL_DISABLED`, and
then writes `REG_A5XX_GPMU_GPMU_VOLTAGE = 0x80000000 | 0` under the comment
*"Until we get clock scaling 0 is always the active power level"* — plus
`AGC_MSG_PAYLOAD(2/3)` = the mvolts and MHz of `fast_rate`. **That comment is
stale: we have devfreq clock scaling now** (it is the whole of patch `0067`), so
the GPMU's adaptive model is fed a constant "max level, max volts" and has its
throttling off. The per-part leakage fuse (`qcom,gpu-efuse-leakage`,
`base-leakage-coefficient = 34`) is read for a530 and **not** for a540.
Connect this to patch `0211`'s own motivation — *"Adreno 540 hangs clustered at
710 MHz with the SoC above 70 C"*.

**Layers 3-4 — the policy is skin, not junction.** Two independent vendor
sources agree: `thermal-engine.conf` caps the big cluster
1804800/1497600/1190400/902400/300000 and the GPU 414/342/257 MHz off
`bd_therm2`, and `thermal_info_config.json` gives `bd_therm2` Type SKIN with
LIGHT 38 / MODERATE 45 / SEVERE 48 / CRITICAL 50 / EMERGENCY 52 / SHUTDOWN 54 C
while **tsens CPU/GPU only reach SEVERE at 95 C and SHUTDOWN at 125 C**.
Junction is a backstop. It was never the policy.

**What this rules out** —
- *"More trip points fixes the heat."* It improves layer 3 only, and layer 3
  cannot work properly at all without a skin sensor — see
  [[taimens-skin-sensor-is-one-devicetree-change]].
- *"The GPU has no hardware power limiter on this part."* It has one, on die,
  and we turn it off in two register fields.
- *"msm8998 thermal policy is junction-based."* Backwards.

**How it was established** — vendor source and DT read directly; both userspace
configs extracted from the factory `vendor` partition with `debugfs`. Measured
on taimen 2026-09-19 (kernel 7.2.2 #57): a 180 s 8-thread CPU burn held
73.5-75.1 C, 4.29 W at the USB input, with the cooling device at state 0 in 71
of 84 samples and never past 2 of 29. Overturned by: a mainline arm where any
of layers 1-3 demonstrably engages below 75 C.
