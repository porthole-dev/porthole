---
id: a540-lm-is-mostly-vendor-parity
title: Three of the four a540 GPMU limiter "gaps" are vendor parity; only the throttle bit and the stale power level are real
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: "Read against ref/downstream-wahoo (android-msm-wahoo-4.4-qt-qpr3, the shipping Pixel 2 XL kernel) 2026-09-19: drivers/gpu/msm/adreno-gpulist.h:243-285 (both a540 entries), adreno.c:100-102 (default pwrctrl_flag), adreno_a5xx.c:1519-1565 (a540_lm_init), adreno_a5xx.h:236-240 (lm_on()), adreno_a5xx.c:1604-1640 (a5xx_pwrlevel_change_settings), adreno_a5xx.h:212-214 (AGC_LEVEL_CONFIG, LM_DCVS_LIMIT). Compared against mainline drivers/gpu/drm/msm/adreno/a5xx_power.c a540_lm_setup() and msm_gpu.h:81 (gpu_set_freq)."
refutes: "the a540 GPMU limiter is disabled in mainline and the vendor runs it; the BCL being disabled for a540 is a mainline shortcut; mainline fails to read the per-part GPU leakage fuse that the vendor reads; a540 LM is a single four-part parity gap"
first-learned: 2026-09-19
---

**The question** — mainline's `a540_lm_setup()` reads like a bring-up stub: it
sets `AGC_LM_CONFIG_BCL_DISABLED` and `AGC_LM_CONFIG_THROTTLE_DISABLE` under
comments that say "isn't enabled for A540" and "for now", pins the GPMU to
power level 0 "until we get clock scaling", and never touches the per-part
leakage fuse that `a530_lm_setup()` writes. Taimen's DT carries
`qcom,lm-limit = <6000>`, `qcom,base-leakage-coefficient = <34>`,
`qcom,gpu-efuse-leakage = <0x00070130 24>` and `qcom,max-power = <5448>`, so it
looks like four values the vendor uses and we throw away. Which of those does
the vendor actually do differently?

**The answer** — **one and a half of the four.** The vendor's own
`a540_lm_init()` is much closer to ours than the comments suggest, because
**a540 does not carry the `ADRENO_LM` feature at all**. `adreno-gpulist.h:243`
and `:265` — both a540 entries, patchid 0 and ANY_ID:

```c
.features = ADRENO_PREEMPTION | ADRENO_64BIT |
	ADRENO_CONTENT_PROTECTION |
	ADRENO_GPMU | ADRENO_SPTP_PC,
```

No `ADRENO_LM`, and no `.lm_major`/`.lm_minor` (the a530 entries above it have
all three). `lm_on()` is `ADRENO_FEATURE(ADRENO_LM) && test_bit(ADRENO_LM_CTRL)`,
so on this GPU it is **false**, and in `a540_lm_init()`:

| what | vendor | mainline | verdict |
|---|---|---|---|
| `AGC_BCL_DISABLED` | always set (`adreno_a5xx.c:1522`) | always set | **parity** |
| `ENABLE_GPMU_ADAPTIVE \| ISENSE_ENABLE` | behind `lm_on()` → **never** on a540 | never | **parity** |
| per-part leakage fuse → `GPMU_BASE_LEAKAGE` | `a530_lm_init()` only; `a540_lm_init()` does not write it | not written | **parity** |
| `GPMU_PWR_THRESHOLD` | `PWR_THRESHOLD_VALID \| lm_limit()`, `lm_limit()` = DT `qcom,lm-limit` = 6000 | hardcoded `0x80000000 \| 6000` | **parity by value** |
| `AGC_THROTTLE_DISABLE` | set only `if (!test_bit(ADRENO_THROTTLING_CTRL))`, and `adreno.c:100-102` has that bit **set by default** → vendor does **not** set it | set unconditionally | **REAL GAP** |
| `GPMU_GPMU_VOLTAGE` | `0x80000000 \| active_pwrlevel`, then re-written on **every** level change | `0x80000000 \| 0`, once, never again | **REAL GAP** |
| voltage table payload | `_write_voltage_table()` writes `max_power`, `levels`, then (mV, MHz) for **every** pwrlevel | `max_power`, `1`, one (mV, MHz) pair at `fast_rate` | **REAL GAP** (same shape as above) |
| `AGC_LEVEL_CONFIG` | `~(GENMASK(LM_DCVS_LIMIT,0) \| GENMASK(16+LM_DCVS_LIMIT,16))` with `LM_DCVS_LIMIT 1` = `~0x30003` | `LEVEL_CONFIG ~(0x303)` | **differs** — mainline's second field is at bit 8, vendor's at bit 16 |
| `GPMU_VOLTAGE_INTR_EN_MASK` | written at the end of `a540_lm_init()` | written in `a5xx_gpmu_init()` instead | parity, different place |

The level gap has a second half that is not in `a5xx_power.c` at all. Vendor
`a5xx_pwrlevel_change_settings()` (`adreno_a5xx.c:1604-1640`) runs on **every**
power-level transition, and for a540 it is gated on nothing but `ADRENO_GPMU`:

```c
if (ADRENO_FEATURE(adreno_dev, ADRENO_GPMU)) {
	if (adreno_is_a540(adreno_dev))
		on = ADRENO_GPMU;
}
...
gpmu_set_level(adreno_dev, (0x80000010 | postlevel));   /* pre  */
gpmu_set_level(adreno_dev, (0x80000000 | postlevel));   /* post */
```

and `gpmu_set_level()` polls bit 31 until the GPMU acknowledges, 100 tries.
Mainline a5xx implements no `gpu_set_freq` callback at all (`msm_gpu.h:81` — only
a6xx does), so there is no place this could happen today: since devfreq scaling
landed, the GPU changes frequency behind a GPMU that still believes it is at
level 0 and at `fast_rate` volts.

**What this rules out** — three quarters of the "GPU on-die limiter is
disabled" gap as previously written up. Do not go looking for the leakage fuse
read, do not enable BCL, and do not enable the adaptive/ISENSE bits: the
vendor does none of those on this GPU, and the DT properties that suggest
otherwise (`base-leakage-coefficient`, `gpu-efuse-leakage`) are read by code
paths a540 never reaches. What is left is worth doing, and is two changes, not
four: clear `AGC_THROTTLE_DISABLE`, and give a5xx a `gpu_set_freq` that writes
the real level to `GPMU_GPMU_VOLTAGE` with the vendor's pre/post pair and ack
poll — which also makes writing the full voltage table meaningful.

**How it was established** — read, not measured: both trees are source. The
decisive line is the absence of `ADRENO_LM` from the a540 gpulist entries,
because everything else follows from `lm_on()` being false. It would be
overturned by a wahoo-specific override setting `ADRENO_LM` or clearing
`ADRENO_THROTTLING_CTRL` somewhere outside `adreno.c`'s initialiser. `grep
pwrctrl_flag` over the vendor tree finds only the default and its readers, and
the two kgsl knobs are also settable from userspace — but a case-insensitive
grep for `kgsl|throttl|adreno|devfreq` over the whole pulled `/vendor/etc`
(`blobs/work/vendor-etc/`) finds no write to either: `powerhint.json` touches
only `kgsl-3d0/devfreq/{min,max}_freq`, and `init.taimen.rc` mentions kgsl
nowhere at all.
