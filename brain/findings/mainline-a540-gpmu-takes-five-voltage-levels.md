---
id: mainline-a540-gpmu-takes-five-voltage-levels
title: Mainline's a540_gpmu.fw2 accepts five voltage-table levels; a sixth stops the GPMU booting at all
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: taimen 7.2.2, one boot per arm via porthole build mod + reboot, dmesg read only after 45 s uptime, 2026-09-20: stock clean; AGC_THROTTLE_DISABLE cleared clean; +AGC_LEVEL_CONFIG at bit 16 clean; +3-level table clean; +5-level table clean; +6-level table TIMES OUT; +7-level table TIMES OUT. Error is 'GPMU firmware initialization timed out' from a5xx_gpmu_init()'s spin_usecs(25) on 0xBABEFACE in GPMU_GENERAL_0, at t=14.5-15.1 s.
refutes: the a540 GPMU can be given the vendor's whole voltage table; GPMU_GPMU_VOLTAGE is not acknowledged on mainline; a5xx gpu_set_freq is what wedges the GPMU; a clean dmesg right after ph-reboot means the GPU came up clean
first-learned: 2026-09-20
---

**The question** — [[a540-lm-is-mostly-vendor-parity]] named two real deltas
on the a540's on-die limiter: clear `AGC_THROTTLE_DISABLE`, and write the whole
voltage table instead of mainline's single entry, as the vendor's
`_write_voltage_table()` does. Port them. Do they work?

**The answer** — the throttle bit is free; **the table has a hard limit of five
levels, and exceeding it stops the GPMU booting.** With six or more (mV, MHz)
pairs, `a5xx_gpmu_init()` never sees `0xBABEFACE` in `GPMU_GENERAL_0` within
its `spin_usecs(gpu, 25, ...)`, logs

```
[drm:a5xx_power_init] *ERROR* 05040001: GPMU firmware initialization timed out
```

and the GPMU does not run for the rest of the boot. taimen has seven GPU OPPs,
so writing "the whole table" lands exactly on the wrong side of the line.

| voltage-table levels written | GPMU init |
|---|---|
| 1 (mainline stock) | clean |
| 3 | clean |
| **5** | **clean** |
| 6 | timed out |
| 7 (the vendor's own count) | timed out |

The vendor writes all seven on this same silicon -- against **its own GPMU
build**. This bound belongs to `a540_gpmu.fw2` as shipped in linux-firmware,
not to the hardware. So `AGC_MAX_LEVELS 5`: still five times what mainline
told it, and inside the firmware's parser.

**What this rules out** —

- *"`GPMU_GPMU_VOLTAGE` is not acknowledged on mainline."* It was never asked
  while the GPMU was running. The 1162 "did not ack" errors seen with a
  `gpu_set_freq` were a **consequence** of the GPMU never having booted, which
  the oversized table caused.
- *"a5xx `gpu_set_freq` is what wedges the GPMU."* It is not, and an earlier
  version of this note said it did. It IS still the wrong hook -- devfreq calls
  `->target()` on every 50 ms poll rather than per transition, so it busy-waits
  under `df->lock` ~100 times a second for a frequency that did not move -- but
  that is a separate objection and the wedge was the table.
- *"The vendor's table size is portable."* It is not.

**How it was established** — one boot per arm, `porthole build mod
drivers/gpu/drm/msm/msm.ko msm --yes` plus a reboot (the compositor holds msm,
so it will not unload in place), bisecting the level cap.

**The trap that made the first pass of this WRONG, and it is worth more than
the finding:** `a5xx_power_init()` runs at **t = 14.5-15.1 s**, and
`tools/ph-reboot.sh` returns as soon as ssh answers, at **t ~ 14 s**. A
`dmesg | grep -i gpmu` issued immediately after the reboot therefore reports a
clean boot on a kernel that is about to fail. Five arms were scored clean that
way and the bisect pointed at the wrong change. **Wait for
`/proc/uptime` to pass 45 s before reading.** See
[[an-instrument-that-fails-quietly-is-worse-than-none]].

Overturned by: a different `a540_gpmu.fw2`. If linux-firmware ever ships the
vendor's build, re-measure the cap before raising it.
