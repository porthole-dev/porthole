---
id: the-wake-crash-dies-inside-a5xx-hw-init
title: The display-wake crash dies inside a5xx_hw_init() -- it IS a GPU register access, and the instrument that said otherwise could not see this window
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: logs/netconsole-gpu (3 SError panics, all in a5xx_hw_init on the submit path), logs/2026-08-27-slow (1 in a5xx_gpu_busy), logs/gpu-probe (10 oracle runs, 11 stage crumbs inside hw_init; 6 deaths at 4 different stages)
refutes: the wake crash is not a GPU register access; the offending access is in some other register block; the question to chase is which devfreq callback is at fault; one specific register write is the trigger
first-learned: 2026-08-27
---

> **CLOSED, same evening.** Everything below stands and was the step that got
> there, but the question is now answered: the GPU is *not* powered down -- a
> suspend/resume pair as little as 80 us apart leaves the block live, and
> `a5xx_hw_init()` reprograms `CP_RB_BASE` under a running CP. Fix measured at
> 300/300 wake cycles. See [[a-short-power-collapse-leaves-the-a5xx-cp-alive]].

**The question** — the display-wake reset was chased through devfreq for two
sessions on the strength of one claim: that it is *not* a GPU register access.
Where does the machine actually die?

**Inside `a5xx_hw_init()`, on the submit path, every time it leaves a trace.**
Four panics were captured on 2026-08-27 and every one of them is an
asynchronous external abort (`SError ... code 0x00000000bf000002`) taken by a
CPU that was doing GPU MMIO:

    where                                        arm
    gpu_write   <- a5xx_hw_init+0x940/0x10cc     devfreq stub build
    gpu_write   <- a5xx_hw_init+0x940/0x10cc     devfreq stub build
    a5xx_hw_init+0x1248/0x46b8 (gpu_write inlined)  devfreq stub build
    clk_get_rate <- a5xx_gpu_busy <- msm_devfreq_get_dev_status   r89 baseline

All three hw_init ones arrive through
`msm_job_run -> msm_gpu_submit -> msm_gpu_hw_init`. Disassembling the surviving
`.output/.../msm.ko` puts `+0x1248` on the `gpu_write(gpu,
REG_A5XX_RBBM_INT_0_MASK, A5XX_INT_MASK)` near the end of the function
(`str w9=0x1190037e` into `mmio + 0xe0`; a5xx.xml offset 0x0038 is
`RBBM_INT_0_MASK`, and `A5XX_INT_MASK` is that value).

**Why the earlier instrument said the opposite.** It named "any caller touching
the GPU while it is not runtime active, excluding the PM callback itself". By
the time `a5xx_hw_init()` runs, `pm_runtime_get_sync()` has already returned and
`runtime_status` reads **active** -- so the one function where the crash
actually lives is exactly the one that check can never flag. The instrument was
not wrong about what it measured; it could not see this window at all.
(`brain/laws/the-instrument-is-guilty.md`.)

**Confirmed a second way, on a silent death.** A fresh build put two crumbs in
`a5xx_hw_init()` -- one at entry, one immediately before the `RBBM_INT_0_MASK`
write -- each printing `RBBM_STATUS`. Over three oracle runs netconsole carried
**57 complete `enter -> irqs` pairs and exactly one orphan**, and the orphan is
the death: cycle 22, `enter RBBM_STATUS=00000001`, then nothing. A second run
died the same way at cycle 18. So this reproduces on a plain watchdog reset with
no panic at all, which is the common case.

**Two things fall out of the entry crumb.** `RBBM_STATUS` reads `0x00000001`,
not `0xdeadbeef`, on every one of those 57 resumes *and on the dying one*: the
GPU register window is **reachable when `a5xx_hw_init()` starts** and stops
being reachable part way through. So this is not "the GPU was left powered
down" -- something takes the block away underneath a function that is already
running.

**What this rules out** — measured, not argued:

- **"It is not a GPU register access; the offending access is in some other
  register block."** Four captures say otherwise. Superseded in
  [[the-wake-crash-is-devfreq-not-a-register-access]]; the rest of that note
  still stands.
- **"The next question is which devfreq callback is at fault."** All three were
  stubbed ([[the-wake-crash-is-not-in-msms-devfreq-callbacks]]) and the three
  hw_init panics above are *from those very stub builds*. With devfreq's poll
  taken out of the picture the crash simply reappears on the next-largest
  consumer of GPU MMIO, which is `hw_init`. devfreq was never the mechanism --
  it was a second place to be standing when the block went away.
- **"The window is dead on entry to hw_init."** `RBBM_STATUS=00000001` at the
  entry crumb, including on the run that died.

**How it was established** — `tools/tk-wake-cycle.py` for the wakes,
`tools/tk-capture.sh` for netconsole (verified end to end before every run), and
two `pr_info` crumbs reading `RBBM_STATUS` inside `a5xx_hw_init()`, built with
`porthole build mod drivers/gpu/drm/msm/msm.ko msm` against
`PORTHOLE_KERNEL_TREE=.../worktrees/tk-voice`. The control is the pair count:
56 of 57 resumes printed both crumbs, so a missing second crumb is a death and
not a dropped UDP packet. What would overturn it: an orphan `enter` on a run
that did **not** reset, or an `enter` crumb reading `0xdeadbeef`.

**It is not one bad register write.** `a5xx_hw_init()` was then instrumented
with eleven crumbs -- entry, `hwcg`, `adreno`, `gpmu`, `cpfw`, one before each of
the five CP firmware/ringbuffer base writes, `preempt`, `irqs` -- each printing
`RBBM_STATUS`. Seven deaths landed at four different places:

    last crumb reached   how it died      cycle
    cpfw                 silent reset      2
    cpfw                 SError panic     10
    cp_cntl              SError panic     18
    irqs                 silent reset      9
    enter (2-crumb build) silent reset    22
    enter (2-crumb build) silent reset    18
    irqs  (2-crumb build) silent reset    96

and yesterday's panics sat at `a5xx_hw_init+0x940` and `+0x1248`, elsewhere
again. **`RBBM_STATUS` reads `0x00000001` at every crumb up to the last one.**
So the window is healthy right up to the moment it is not, and the stage that
gets blamed is simply wherever the CPU happened to be. Nothing in these five CP
writes is special -- they are plain stores of IOVAs into the CP block.

**What the abort actually is.** The 19:45 panic decodes cleanly. The reported
`(P)` frame is `a5xx_hw_init+0x24fc`, which disassembles to
`ldr w2, [x8]` with `x8 = gpu->mmio + 0x13d4` -- register `0x4f5`, which
a5xx.xml names **`RBBM_STATUS`: the crumb's own read**, the instruction right
after the five CP writes. `x2` holds `0x96000210` = EC 0x25 data abort,
DFSC 0x10 **synchronous external abort**, WnR 0 -- **a read**. The stack shows
`el1h_64_sync` first and the async SError arriving on top of it while the sync
handler ran.

**And there is a stall before it.** The crumbs are 200-350 us apart (netconsole
costs that much per line), then:

    65.694506  cpfw   RBBM_STATUS=00000001
    65.701530  SError                          <- 7.0 ms later

Five register writes do not take 7 ms. A posted write stalling for milliseconds
and the next read taking an external abort is what a slave that has gone away
looks like from the CPU.

**So the shape of the bug is: the GPU register window is removed asynchronously,
while `a5xx_hw_init()` is running and runtime PM says the device is active.**
Not a stray access to a powered-down block, not a specific register, not
devfreq. Something else drops the block underneath a live function.

**Where to go next** — find what takes the window away. The GPU node carries
only `power-domains = <&rpmpd MSM8998_VDDMX>`; its register window at
`0x05000000` needs `GCC_GPU_CFG_AHB_CLK` (the "iface" clock) and the GPU
GDSCs, and `5040000.iommu` -- not the GPU node -- is what owns `GPU_GX_GDSC`.
Three candidates, none yet measured:

1. **The SMMU runtime-suspending underneath hw_init.** arm-smmu device-links
   the GPU to it, so this should not be possible -- prove the link exists at
   runtime rather than assuming it (`/sys/class/devlink/`), and log
   `5040000.iommu`'s `runtime_status` from the crumb.
2. **`gcc_gpu_cfg_ahb_clk` being gated.** Log its enable count and rate in the
   crumb; it is the one clock the register window cannot live without.
3. **The GPMU collapsing SPTP/RAC on its own.** It is not reset across runtime
   suspend, so it may still be running from the previous session while
   `a5xx_hw_init()` reprograms the block under it.

The crumb is the right instrument for all three: it already runs once per stage
per wake and netconsole carries it off a phone that then dies silently.

Related: [[holding-vdd-mx-does-not-stop-the-wake-crash]],
[[the-wake-crash-is-not-in-msms-devfreq-callbacks]],
[[the-wake-crash-is-devfreq-not-a-register-access]].
