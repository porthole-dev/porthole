---
id: gold-osm-acd-autoxfer-timeout
title: The gold OSM -110 is the ACD auto-transfer poll, and four tempting causes are dead
scope: soc:msm8998
subsystem: cpufreq
severity: finding
confidence: proven
evidence: taimen logs/2026-08-27-slow/osm-boot0.txt; in-kernel timing on pkgrel 96 (#97), 2026-08-27: silver autoxfer 10989 ns, gold 6875 ns, single-register xfers 3750-6146 ns -- every one of them past the 3 us the old poll nominally allowed
refutes: the 3us timeout is simply too tight; the gold cluster is power-collapsed so the transfer cannot land; the transfer is clocked by the cluster clock and is slow at low OPPs; mainline programs the wrong ACD values for gold
first-learned: 2026-08-27
---

**The question** — on roughly 4% of boots (3 of 76) the gold cpufreq domain
aborts with `qcom-cpufreq-hw: Cannot setup the OSM for CPU4: -110`, `policy4`
never appears, the big cluster runs uncontrolled at whatever clock the boot
chain left, and the phone feels like a silver-only device. CPU5/6/7 then fail
`-19` as a consequence. Silver never fails this way.

**The answer** — the `-110` is `qcom_cpufreq_hw_acd_write_autoxfer()`
(`drivers/cpufreq/qcom-cpufreq-hw.c:779`) giving up on the ACD master→local
auto-transfer. Proven by the step markers, not inferred: the failing boot
prints `domain 1 step 7 ok (fsm-enable)` and never prints `step 8 ok
(acd-regs)`, and that call is the only fallible thing between them
(`:1651`→`:1655`).

The poll is `readl_poll_timeout(..., sleep_us=1, timeout_us=3)`. Read
`poll_timeout_us()` in `include/linux/iopoll.h` carefully: it evaluates
`__expired` *before* the read and returns success if the condition holds even
after expiry. So the driver does **not** get 3 µs — it gets exactly **two
reads**, one at t≈0 and one after a single `usleep_range(1, 1)`, an hrtimer
sleep whose real length is tens of microseconds and varies boot to boot. The
nominal 3 µs is not the budget; the hrtimer wakeup latency is. That is the
only boot-to-boot random quantity in the path.

**What this rules out** —

- *"The 3 µs timeout is simply too tight, raise the constant."* No. Because of
  the re-read after expiry the effective budget is already tens of µs, and the
  vendor's own poll (`ref/downstream-wahoo/drivers/clk/qcom/clk-cpu-osm.c:571`)
  is **smaller**: `ACD_LOCAL_TRANSFER_TIMEOUT_NS * numregs` = 500 ns × 4 = 2 µs.
  Mainline is already more generous than the vendor. Raising the number alone
  does not explain why it ever passes.
- *"The gold cluster is power-collapsed, so the local copies are unreachable."*
  Tempting — the vendor comment at `clk-cpu-osm.c:3021` says these registers
  "must be copied from master to local copy on PC exit", offsets below
  `ACD_MASTER_ONLY_REG_ADDR` (0x80) are the transferrable ones, and gold does
  spend real time in `cpu-sleep-1-1` (CPU + L2 power collapse). **Measured
  dead:** pulsing the transfer with all four gold cores pinned in a busy loop —
  0.0% idle duty in *every* state — still stalls ~10% of pulses. Collapse is not
  necessary for a stall.
- *"The transfer is clocked by the cluster clock, so it is slow at low OPPs."*
  **Measured dead:** transfer completion is flat at p50 ≈ 18.9 µs with gold
  pinned at 300 MHz, 806 MHz, 1.34 GHz and 2.36 GHz — a 7.8× frequency range,
  identical to three significant figures. (That flatness is also the tell that
  18.9 µs is the *instrument* floor: a userspace /dev/mem read costs ~2.8 µs and
  `perf_counter_ns()` about the same, so p50 is "broke out on the first read".)
- *"Mainline programs the wrong ACD values for the gold cluster."* Dead on
  inspection: `msm8998-v2.dtsi:41-46` gives both clusters identical
  `acd{td,cr,sscr,extint0,extint1,autoxfer}-val`, matching the hardcoded
  `msm8998_soc_data.acd_data` exactly, and both blocks read back correct on a
  good boot (`ACDCR 0x2b5ffd`, `ACDTD 0x9611`, `ACDSSCR 0x501`,
  `EXTINT 0x2cf9afe`, `GFMUX 1`, `AUTOXFER_CFG 0x9406`).

**How it was established** — `tools/tk-regdump.py` for the register state (note
its base must be page-aligned: use `0x17814000 0x800 0x100` for gold, not
`0x17814800`), plus a `/dev/mem` probe that pulses the AUTOXFER trigger (+0x84)
only, never the CFG mask (+0x80), run under `chrt -f 99 taskset -c 0`. Idle
state was controlled through `cpuidle/state*/disable` and read back per arm, so
each arm proves which state it actually exercised.

**The measurement that closed it** — instrumenting the poll in-kernel and
reading it at boot (patch 0187, pkgrel 96, running as `#97`):

    cpu0 (silver)  acd autoxfer: 10989 ns   xfer: 3802 / 3750 / 3750 / 4219 ns
    cpu4 (gold)    acd autoxfer:  6875 ns   xfer: 3854 / 3750 / 6146 / 3750 ns

The transfer needs 7-11 us.  The old code allowed 3 us.  **It expired on
every boot that ever ran, on both clusters.**  It passed anyway only because
`poll_timeout_us()` accepts a post-expiry read, so the single
`usleep_range(1, 1)` between the two reads is what actually decided the
outcome -- an hrtimer asked for 1 us, usually delivering tens.  When that
sleep landed shorter than the transfer, the second read found the bit still
clear and the domain died with -ETIMEDOUT.  Nothing about the hardware was
marginal; the timeout was undefined and worked by accident.

The fix busy-polls both ACD status registers through one helper with a
budget of 1 ms that is actually 1 ms -- roughly 100x the worst transfer
observed -- and logs the measured time, so a regression here shows up as a
number instead of as an intermittent boot failure.

**Still open, and named rather than hidden** — this does not explain why
only domain 1 was ever *seen* failing.  Silver's transfer measured slower
here (11.0 us vs 6.9 us), so "gold is slower" is dead alongside the four
theories above.  The likeliest reading is sampling: 3 failures in 76 boots
is a low enough rate that 0 silver failures in the same 76 is unremarkable.
That is a guess, not a measurement.

**Two traps this cost.** A `kill %1` inside a non-interactive `tk_run` shell
does nothing — job control is off, the spinners survive, and three "different"
arms silently become one. And `pkill -f <pattern>` matches the ssh shell whose
command line *contains* the pattern; `pkill -f 'wh[i]le :'` is the fix.
