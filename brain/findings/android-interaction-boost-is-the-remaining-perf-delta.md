---
id: android-interaction-boost-is-the-remaining-perf-delta
title: Android's INTERACTION boost is the remaining perf delta -- the scaling infrastructure already has vendor parity
scope: soc:msm8998
subsystem: cpufreq
severity: finding
confidence: proven
evidence: tk-gesture-bench grid-fling x3 per config on identical UI state, 2026-09-01; vendor policy read from the taimen factory vendor image via debugfs (powerhint.json and the vendor init files)
refutes: the GPU is underclocked vs Android; the big cluster is missing a top OPP; mainline lacks CPU-to-DDR bandwidth voting; the vendor kernel scales something mainline cannot
first-learned: 2026-09-01
---

**The question** — Android on this SoC is buttery smooth and mainline is not.
What does the vendor stack (kernel + PowerHAL) do for CPU/GPU/DDR scaling that
the mainline port is missing?

**The answer** — Almost nothing structural. The one behavioural gap that
measures is Android's INTERACTION power hint: on every touch, PowerHAL floors
both CPU clusters at ~1.13 GHz AND holds /dev/cpu_dma_latency at 44 µs
(forbidding idle states deeper than ~WFI). Reproducing exactly that pair on
phosh (floors 1134000/1132800 + a process holding cpu_dma_latency=44) took
grid-fling from 4 janks >33 ms across 3 runs (worst frame 66 ms) to 1 across 3,
tightened p90 18.0-18.3 -> 17.1-18.0 ms, and cut touch-after-idle worst-case
from 61 ms to 25 ms. Neither half alone works: floors-only still janked 2/run,
dma-only 3/run -- which is WHY Android ships them as one hint.

**What this rules out** —
- *"Android runs the GPU lower/smarter and we misconfigured devfreq."* Dead.
  Mainline trans_stat shows 257->710 MHz direct jumps (4016 of them) and most
  active time AT 710; Android's adreno-tz started at initial-pwrlevel 257 MHz
  and hovered mid-table. Our GPU scaling is MORE aggressive than vendor.
- *"The big cluster is missing its 2.4576 GHz top OPP."* Dead. powerhint.json's
  2457600 is a clamp sentinel above Android's own table; the vendor kernel's
  real gold max equals mainline's 2361600.
- *"Nothing votes DDR bandwidth for the CPU on mainline."* Dead. The upstream
  icc-bwmon node (pmu@1008000, qcom,msm8998-bwmon) is present, loaded, and
  under 3x dd ramps its vote 1144000 -> 13763000 kBps -- the full range of
  Android's bw_hwmon mbps_zones table.
- *"zram/VM tuning lags Android."* Backwards: pmOS runs 5.6 G lz4 swappiness
  180 vs Android's 2 G lz4 swappiness 100; page-cluster 0 both.

**Remaining real deltas, none proven to jank yet**: no LAUNCH hint (Android
pins the big cluster at max for 5 s on app launch + read_ahead_kb 2048 during
boot); schedutil single rate_limit_us=2000 vs Android's asymmetric 500/20000;
Android's schedtune top-app boost=10 + background cpuset confinement to cpu0-1
(approximable with cgroup uclamp.min / AllowedCPUs); skin-temp stepped
throttling vs our junction 85C trip.

**How it was established** — taimen factory vendor.raw read with debugfs (no
root): /etc/powerhint.json is the entire hint table, /etc/init/hw/init.taimen.rc
the boot sysfs writes, the vendor power script under /bin the bus-DCVS setup. Device side:
tk-gesture-bench.py grid-fling 4 / latency, three runs per config on the same
UI state (MobileSettings open -- the latency scene's centre-screen tap LAUNCHES
an app and changes the state for the next run; relaunch it before comparing).
Overturned by: a jank distribution that does not shrink under floors+dma on a
clean boot.
