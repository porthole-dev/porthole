---
id: a-short-power-collapse-leaves-the-a5xx-cp-alive
title: The display-wake reset: a runtime power collapse too short to discharge GX leaves the a5xx CP alive, and hw_init reprograms CP_RB_BASE underneath it
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: logs/gpu-probe: 445 RBBM_STATUS samples at hw_init crumbs, 443 read 00000001 and the 2 that read c00003c1 are the deaths; suspend-to-resume gap 80us and 1.37ms on the two deaths against a 275ms median; fix measured 100/100 three times (300 transitions) against a no-reset series of 2/9/10/12/18/18/20
refutes: the wake crash is a devfreq problem; it is VDD_MX; it is not a GPU register access; raising min_freq or the autosuspend delay is the fix; the GPU is powered down when the offending access happens
first-learned: 2026-08-27
---

**The question** — the phone resets when the screen is woken, roughly one wake
in twenty, usually with nothing on any console. Two sessions chased this
through devfreq and through VDD_MX. What is actually happening?

**The GPU never powers down.** Runtime PM suspends and resumes the GPU as a
pair sometimes only **80 us** apart. That is far too short for the GX rail to
discharge, so the block keeps its state: `CP_RB_CNTL` still holds the previous
session's ring configuration and the CP's ME is still un-halted.
`a5xx_hw_init()` is written on the opposite assumption -- that it is bringing
up a block that came back reset -- so it writes the new `CP_RB_BASE` into a
live CP that has no valid ring configuration to go with it. The CP starts
fetching packets from that ring, the VBIF fills with traffic nothing answers,
and the next access to the GPU takes an external abort that resets the SoC.

The transition is visible in one register, read at every stage of `hw_init`:

    61.941276  cpu7  pm_suspend: done                          rail dropped
    61.941356  cpu6  a5xx_pm_resume enter                       80 us later
    61.943426  cpu6  hw_init enter    RBBM_STATUS=00000001      idle
    61.943804  cpu6  hw_init cp_rb    RBBM_STATUS=00000001      idle
                     gpu_write64(CP_RB_BASE, ...)
    61.943847  cpu6  hw_init cp_cntl  RBBM_STATUS=c00003c1      CP_BUSY|VBIF_BUSY
                     gpu_write(CP_RB_CNTL, ...)                 too late
               <external abort, reset>

`0xc00003c1` decodes as GPU_BUSY_IGN_AHB, GPU_BUSY_IGN_AHB_CP,
GPU_BUSY_IGN_AHB_HYST, VBIF_BUSY, CP_BUSY, CP_BUSY_IGN_HYST, HI_BUSY. A
genuinely idle a5xx reads `0x00000001` (HI_BUSY alone).

**The controls.** Across 445 `RBBM_STATUS` samples taken at `hw_init` stage
crumbs, **443 read `00000001` and the only 2 that did not are the two deaths**,
both at the same crumb. Of 31 suspend-to-resume gaps, **one was under a
millisecond** -- 80 us, against a median of 275 ms -- and that is the one that
died; the second death's gap was 1.37 ms, also in the bottom tenth.

**The fix, measured.** Put the GPU through the software reset it should already
have had, at the top of `a5xx_hw_init()`, before anything is programmed. It is
the same pulse `a5xx_recover()` already uses to get a wedged a5xx back:

        gpu_write(gpu, REG_A5XX_RBBM_SW_RESET_CMD, 1);
        gpu_read(gpu, REG_A5XX_RBBM_SW_RESET_CMD);
        gpu_write(gpu, REG_A5XX_RBBM_SW_RESET_CMD, 0);

    arm                                          result
    r89 aport baseline                           died at cycle 45
    same tree, crumbs only, no reset             died at 2, 9, 10, 12, 18, 18, 20
    + the reset above                            **100/100, three times running**

Three back-to-back 100-cycle runs -- 96, 99 and 99 of those wakes with
`gpu=suspended` read before the press, so the collapse the bug needs really
happened on essentially every cycle. Zero GPU faults, zero hangchecks, and not
one of the 300 presses was swallowed, so the reset costs nothing on the healthy
path. Against a baseline of 45 and a same-build-without-the-reset series of
2, 9, 10, 12, 18, 18 and 20, surviving 300 consecutive transitions at a ~1-in-20
failure rate is a coincidence with probability around 2e-7.

**What this rules out** — every one of these was measured, and each is a day
someone else does not have to spend:

- **"It is a devfreq problem."** devfreq's poll is required only because it
  keeps the GPU cycling through suspend/resume often enough to hit a short
  pair. All three of msm's devfreq callbacks were stubbed and the crash
  survived, which is what
  [[the-wake-crash-is-not-in-msms-devfreq-callbacks]] measured and could not
  explain.
- **"It is VDD_MX."** Refuted separately in
  [[holding-vdd-mx-does-not-stop-the-wake-crash]]; MX was a coincidence of the
  three immune arms. The thing they really had in common is that in each of
  them the GPU either never collapsed or never collapsed briefly.
- **"It is not a GPU register access."** It is; see
  [[the-wake-crash-dies-inside-a5xx-hw-init]].
- **"Raise `min_freq` or the autosuspend delay."** Neither addresses a
  suspend/resume pair 80 us apart, which is a runtime-PM race, not a cadence.
- **"The GPU is powered down when the offending access happens."** The opposite:
  it is powered *up* and still executing.

**How it was established** — `tools/tk-wake-cycle.py` for the wakes,
`tools/tk-capture.sh` for netconsole, and `MSM_CRUMB()` in `msm_gpu.h` --
crumbs at every stage of `a5xx_hw_init()`, at each step of
`msm_gpu_pm_suspend/resume`, and around `dev_pm_opp_set_rate()`, each carrying
`raw_smp_processor_id()` so concurrent work on another CPU would be visible.
Built with `porthole build mod drivers/gpu/drm/msm/msm.ko msm` against
`PORTHOLE_KERNEL_TREE=.../worktrees/tk-voice`. What would overturn it: a death
whose preceding suspend-to-resume gap is a normal one, or a `c00003c1` on a
resume that does not reset.

**What is still open.** The reset is unconditional, so every wake pays for one.
Gating it on a measured suspend-to-resume gap is the obvious refinement, and
the threshold is a hardware property (how long GX takes to discharge) that
wants measuring rather than guessing. It is also worth asking upstream whether
`a5xx_pm_suspend()` should be doing the `RBBM_BLOCK_SW_RESET_CMD` it currently
skips on a540 -- the comment there says the non-a530 parts "tend to lock up",
which may be this same bug seen from the other end.

Related: [[the-wake-crash-dies-inside-a5xx-hw-init]],
[[holding-vdd-mx-does-not-stop-the-wake-crash]],
[[the-wake-crash-is-not-in-msms-devfreq-callbacks]],
[[the-wake-crash-is-devfreq-not-a-register-access]].
