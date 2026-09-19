---
id: eas-never-starts-without-an-energy-model
title: EAS never starts on msm8998: the CPU OPP tables carry CPR corners, not power
scope: soc:msm8998
subsystem: power
severity: finding
confidence: proven
evidence: taimen 2026-09-19, kernel 7.2.2 #57, aport pkgrel=56. /sys/kernel/debug/energy_model exists and is EMPTY (no perf domains). qcom-cpufreq-hw.c:1831 sets .register_em = cpufreq_register_em_with_opp, but a find over /sys/firmware/devicetree/base for opp-microwatt returns nothing and for opp-microvolt returns only gpu@5000000's table; cpu-silver-opp-table and cpu-gold-opp-table carry opp-level (CPR corners) only. dmesg has no 'Energy Aware Scheduling' line. Vendor model present at ref/downstream-wahoo/arch/arm/boot/dts/qcom/msm8998.dtsi:282-415.
refutes: our CPU scaling is at vendor parity; the scheduler already places tasks energy-aware; the battery problem is entirely the missing RPM handshake
first-learned: 2026-09-19
---

**The question** — the battery drains and the SoC gets hot under load. Is the
CPU side at vendor parity? Does the scheduler place work energy-aware on this
big.LITTLE part, the way the Android kernel did?

**The answer** — no, and the reason is one missing DT property class. EAS never
starts. `/sys/kernel/debug/energy_model` is created (the framework is built,
`CONFIG_ENERGY_MODEL=y`) and is **empty**: no perf domain ever registered.
`qcom-cpufreq-hw` does the right thing — `.register_em =
cpufreq_register_em_with_opp` — but that path resolves to
`dev_pm_opp_of_register_em()`, which needs `opp-microwatt`, or failing that
`opp-microvolt`, to compute power per OPP. On msm8998 the CPU OPPs are CPRh
corners: `cpu-silver-opp-table` and `cpu-gold-opp-table` carry `opp-level` and
nothing else. A `find` for `opp-microwatt` anywhere under
`/sys/firmware/devicetree/base` returns nothing; `opp-microvolt` returns only
`soc@0/gpu@5000000/opp-table/*`. Registration fails silently, `build_perf_domains()`
bails, and the scheduler treats a 2.36 GHz A73 and a 1.9 GHz A53 as
interchangeable capacity.

**The vendor shipped the numbers we need.**
`ref/downstream-wahoo/arch/arm/boot/dts/qcom/msm8998.dtsi:282-415` carries a
measured `energy-costs` node — `busy-cost-data` as `<capacity power>` pairs,
silver `65→11 … 419→201` (22 points), gold `129→56 … 1024→1683` (31 points),
plus per-cluster tables and `idle-cost-data`. Our OPP counts match almost 1:1
(`cooling_device0 max_state=21` → 22 silver, `cooling_device1 max_state=29` → 30
gold; the vendor's 31st gold point is the 2457600 clamp sentinel already known
not to be a real OPP).

Two facts fall out of that table and both matter more than the registration bug:

- **The top of the gold cluster is brutally inefficient.** Capacity x2.44
  (419 -> 1024) costs x8.6 the power (196 -> 1683). That is why the vendor's
  thermal-engine caps the big cluster from **38 C skin** rather than from a
  junction trip.
- **Near the silver ceiling the clusters cost the same** (silver@419 = 201,
  gold@419 = 196). Below capacity ~419 placement buys little; above it, it is
  everything.

Calibrated against a measured 8-thread burn on this device (~3.0 W over idle for
4 gold@1024 + 4 silver@419 + both cluster costs = 7805 units), one vendor unit is
**~0.384 mW** — so ~646 mW for a gold core at 2.36 GHz, ~77 mW for a silver core
at 1.9 GHz. EAS needs only consistent *relative* values across domains, so
`opp-microwatt = <unit x 384>` is a defensible vendor-derived table.

**What this rules out** —
- *"Our CPU scaling has vendor parity."* Not on placement. The scaling
  infrastructure does (CPRh + OSM programmed, schedutil, powerhintd reproducing
  Android's INTERACTION/LAUNCH hints) — see
  [[android-interaction-boost-is-the-remaining-perf-delta]] — but the *placement*
  half is absent, and the vendor fenced it twice: EAS **and** cpusets
  (`background` -> cpu0-1, `foreground` -> 0-3,6-7, `init.taimen.rc:505-507`).
- *"The battery problem is entirely the missing RPM handshake."* That is the
  ~1 W idle floor and it is real (`qcom_stats` vmin/vlow `Count: 0`,
  BP-13/BP-14), but it says nothing about power under load, which is where the
  user's heat complaint lives.
- *"`CONFIG_ENERGY_MODEL=y` means the energy model is there."* The directory
  existing is not a perf domain existing. Read
  [[shipped-configuration-is-not-running-configuration]] and then read the
  directory's *contents*: an empty `ls` and a missing path look identical in a
  transcript, and that ambiguity cost a re-check in this very session.

**FIXED AND SHIPPED 2026-09-19** (aport pkgrel 57, patch 0237): `opp-microwatt`
on all 52 CPU OPPs, interpolated from the vendor curves at our OPP frequencies,
each charged CPU_COST + CLUSTER_COST/4, scaled at 384 uW/unit. Verified on
device: `/sys/kernel/debug/energy_model/` now holds `cpu0` and `cpu4`,
`ps:2361600` reads `performance=1024 power=665760 cost=6501`, `ps:1900800` on
cpu0 reads `performance=549 power=83520`, and
`/proc/sys/kernel/sched_energy_aware` is 1. Sanity: 4x gold max + 4x silver max
= 2.997 W against a measured 3.0 W burn delta.

**A separate gap this exposed:** `cpu_capacity` is 549/1024 for silver, i.e. an
A53:A73 ratio of **0.666 per MHz**, where the vendor's measured model implies
419/1024 and **0.508**. We over-state the little cluster by 31%. That is
`capacity-dmips-mhz`, not the energy model, it predates this patch, and it now
matters more because EAS actually reads capacity. Do not change it without a
measurement -- it moves placement and schedutil together.

**How it was established** — `ls -d` and `find` over
`/sys/kernel/debug/energy_model` (exists, empty), `find` over the unflattened DT
for `opp-microwatt`/`opp-microvolt`, `grep register_em` in
`drivers/cpufreq/qcom-cpufreq-hw.c`, and `dmesg | grep -i "energy aware"` (no
line). Overturned by: an `opp-microwatt` table landing and
`/sys/kernel/debug/energy_model` gaining two domains — which is also the
verification step for the fix, and is binary and instant.
