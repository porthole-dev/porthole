---
id: mainline-a540-init-matches-kgsl-except-allflatoptdis
title: Mainline a5xx_hw_init programs the A540 at parity with kgsl's a5xx_start; the one extra mainline write is VPC ALLFLATOPTDIS
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: 2026-09-04, mechanical diff of gpu_write/gpu_rmw in linux-ws a5xx_gpu.c (a5xx_hw_init + a5xx_set_hwcg + a5xx_catalog quirks) against kgsl_regwrite/kgsl_regrmw in ref/downstream-wahoo adreno_a5xx.c (a5xx_start + a5xx_hwcg_set + a540_hwcg_regs) and msm8998-gpu.dtsi. Quirks: kgsl reads them from DT and wahoo sets only qcom,gpu-quirk-lmloadkill-disable; mainline's a540 catalog has only ADRENO_QUIRK_LMLOADKILL_DISABLE. So both set VPC_DBG_ECO_CNTL bit 23 and clear HLSQ_DBG_ECO_CNTL bit 18; neither sets PC_DBG_ECO_CNTL bit 8 (TWO_PASS_USE_WFI), RB_DBG_ECO_CNTL bit 9 (1-SP parts) or SP_DBG_ECO_CNTL bit 25 (a530v1). hwcg: every one of kgsl's 95 a540 values appears with the same value in mainline's a5xx_hwcg (mainline lists 186 entries, later duplicates win), plus the same GPMU delay/hyst, RBBM_CLOCK_CNTL 0xAAA8AA00 and ISDB_CNT 0x182. PC_DBG_ECO_CNTL, CP thresholds, UCHE_MODE_CNTL, CP_CHICKEN_DBG, AHB_CNTL1/2, TPL1/RB MODE_CNTL and UCHE_DBG_ECO_CNTL_2 (hbb) match. Only mainline writes VPC_DBG_ECO_CNTL bit 10 (ALLFLATOPTDIS), twice.
refutes: a kernel-side init or clock-gating difference from the vendor explains the a540 lockups; kgsl applies ECO workarounds mainline lacks; the hwcg tables differ
first-learned: 2026-09-04
---

**The question** -- does the vendor kernel program the A540 differently at
start-up (ECO/chicken bits, clock gating, CP queue thresholds) in a way that
could explain lockups mainline sees and Android does not?

**The answer** -- no. Register by register, mainline's `a5xx_hw_init()` plus
`a5xx_set_hwcg()` write what kgsl's `a5xx_start()` plus `a5xx_hwcg_set()`
write for this part, with the same values. The quirk sets agree because
kgsl takes them from the device tree and wahoo's `msm8998-gpu.dtsi` sets
exactly one, `qcom,gpu-quirk-lmloadkill-disable`, which is the one quirk in
mainline's a540 catalog entry. The single mainline-only write is
`VPC_DBG_ECO_CNTL` bit 10, "disable all flat shading optimisation", which
disables an optimisation rather than enabling one.

**What this rules out** -- the whole family of "kgsl sets a bit mainline
does not" theories for the a540 hangs, and "the clock-gating tables drift".
Together with the register-level comparison kept out of this repository
this leaves, for a software cause: register *values* and packet *order* in
the userspace command stream; and for a hardware cause: the rail, now at
the vendor's CPR ceilings since r31, with the next hang's `.ctx` file as
the arbiter.

**How it was established** -- a python extraction of every
`gpu_write`/`gpu_rmw` and `kgsl_regwrite`/`kgsl_regrmw` in the two init
paths and the two hwcg tables, keyed by register name (the names differ
only by the `REG_` prefix), listing one-sided registers and value
differences. Not compared: GPMU firmware programming (`a5xx_power.c` vs
kgsl's `a5xx_gpmu_start`) and the preemption setup, which mainline does
not use on this device.
