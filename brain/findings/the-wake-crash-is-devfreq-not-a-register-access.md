---
id: the-wake-crash-is-devfreq-not-a-register-access
title: The display-wake crash needs GPU runtime suspend AND devfreq polling -- and it is not a GPU register access
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: logs/2026-08-27-slow, netconsole-gpu, slowcycle oracle: polling off 100 cycles clean, polling on dies at 8-28
refutes: the wake crash is a stray gpu_read/gpu_write while the GPU is off; it is the submit path; it is the SMMU's runtime PM; it is the VBIF failing to halt; raising the GPU autosuspend delay fixes it; devfreq is touching the GPU at a bad time
first-learned: 2026-08-27
---
**The question** — the phone reboots when the screen is woken. Sometimes an
asynchronous SError panic naming an innocent bystander, more often nothing at
all on any console and only `bootreason=watchdog` afterwards. What is touching
what?

**The answer** — two conditions are each necessary, measured with a
reproducer that presses the power key every 2 s and logs the count to
`/dev/kmsg` so netconsole has it even when the machine stops silently
(an injected `KEY_POWER` -- no hands needed):

    GPU runtime PM   devfreq polling   result
    auto             50 ms             died at cycle 21, and at 8, and at 28
    on (pinned)      50 ms             immune, every trial, including a mash
    auto             off (0 ms)        100 cycles, clean

Surviving 100 cycles at a ~1-in-20 rate is a 0.6% coincidence, so **devfreq's
polling is required, and so is the GPU actually power-collapsing**. Roughly one
wake in twenty kills the phone, which is what daily use feels like.

**It is not a GPU register access.** `gpu_read()`/`gpu_write()` were
instrumented to name any caller touching the GPU while it is not runtime
active, excluding the PM callback itself (which legitimately does, while the
device is RESUMING). It never fired -- across many reproductions, with
`panic_on_warn=0` so a WARN would have printed. The offending access is in
some other register block.

**What this rules out** — each of these was measured, not argued:

- **"A stray `gpu_read`/`gpu_write` while the GPU is off."** Instrumented; zero
  hits.
- **"The VBIF failed to halt, so we powered down with traffic in flight."**
  `a5xx_pm_suspend()` threw that result away; it now checks. Zero timeouts.
- **"The SMMU's runtime PM."** `5040000.iommu` holds GPU_GX_GDSC and arm-smmu
  device-links the GPU to it, so pinning the GPU pins the SMMU -- but pinning
  the SMMU *alone* does not help. Its map/unmap and both TLB flush paths take
  a runtime PM reference already.
- **"`get_freq()` reads the frequency before the suspended check."** True, and
  fixed as an experiment -- it changed nothing (died at 28), which fits:
  `gfx3d_clk_src` has no `CLK_GET_RATE_NOCACHE`, so that read is cached, not
  hardware. Change reverted rather than left in as unproven hygiene.
- **"Raise the GPU's autosuspend delay."** It does suppress a *mash*, which is
  how it first looked like a fix. It does not address the real failure: at 2 s
  between presses the GPU is fully suspended before each one, so every wake is
  an ordinary single transition. A mitigation validated against the wrong
  pattern.

**What is fixed, and does not close it** — two real bugs found on the way, both
correct on their own terms, neither sufficient:

1. `msm_gpu_pm_resume()` resumed devfreq when the clocks came up, but a4xx and
   a5xx keep powering internal domains after it returns. Moved to the end of
   each `->pm_resume`.
2. `msm_devfreq_target()`'s non-GMU path called `dev_pm_opp_set_rate()` with no
   suspended check, unlike the GMU path beside it.

**How it was established** — a power-key cycler and a mash script (bursts from
one uinput device; a device created per press is not what the hardware key looks
like, and only the burst shape reproduced what a thumb does). NEITHER WAS COMMITTED and
both are lost; `tools/tk-wake-cycle.py` is the rebuilt cycler and is the
instrument to use now. It counts only real panel transitions and puts every
count on /dev/kmsg. Its baseline on r89 was death at cycle 45. netconsole armed
throughout -- and note that its silence only counts when the listener is
verified up, which cost one wrong conclusion here. ramoops is registered as a
console on this device but `/sys/fs/pstore` is EMPTY after a watchdog reset, so
it is not a witness either.

## The answer: VDD_MX, not devfreq

> **SUPERSEDED, 2026-08-27.** Everything above this line stands. The VDD_MX
> conclusion below does NOT: holding MX enabled, and then holding it at 384 --
> the display-on level -- with the display off was measured and neither is
> immune (died at cycle 22 and 14 against a baseline of 45). See
> [[holding-vdd-mx-does-not-stop-the-wake-crash]] before acting on any of it,
> and in particular before doing the multi-domain rework it proposes.

devfreq was a symptom of the real variable. Three conditions are immune, and
they have exactly one thing in common -- **VDD_MX stays voted**:

    GPU pinned power/control=on   immune   the GPU is itself an MX consumer
    devfreq polling off           immune   nothing re-votes or drops the level
    display kept ON               immune   the DPU holds mx at 384

The last one is the decisive arm, because it leaves everything else alone: the
GPU still suspended and resumed **118 times** (swipes keep the session awake,
the panel never blanks, `gpu=suspended` counted between each) with not one
fault, against a baseline that dies every ~24. That is a 0.7% coincidence.

MX tracks the panel exactly -- `mx off-0` blanked, `mx on 384` lit -- which is
why this bug is display-wake specific and why it looked like the GPU's own
fault for so long. The GPU and the **display controller share the MX domain**,
and the GPU's registers sit behind GX, which the SMMU holds up independently.
So when the panel blanks and MX falls to level 0 while GX is still reachable,
an access into that block is a transaction nothing answers.

The lowest GPU OPP votes `RPM_SMD_LEVEL_MIN_SVS`, and devfreq clamps to it the
moment the GPU idles -- which is why devfreq polling was necessary and why
raising `min_freq` does NOT help: the vote is dropped on suspend either way.

**The fix has precedent in this same DT.** The GPU's power is split across two
devices only because a node could hold one domain: the SMMU node owns
`GPU_GX_GDSC`, the GPU node owns `VDDMX`, and the DTS comment says as much.
`remoteproc@4080000` already shows the multi-domain form:

    power-domains = <&rpmpd MSM8998_VDDCX>, <&rpmpd MSM8998_VDDMX>;
    power-domain-names = "cx", "mx";

and a6xx's GMU already attaches domains by name
(`dev_pm_domain_attach_by_name(dev, "gx"/"cx")`). So the shape is: give the
GPU (or the SMMU, whichever keeps the block reachable) both domains by name,
attach them explicitly, and hold MX across the window rather than letting it
fall to 0 under a live GX.

**Do NOT start that without a fastboot recovery path ready**: it changes probe
ordering on the device that drives the display.

**Where to go next — the poll path is now: `get_dev_status` (frequency read,
cached; `gpu_busy` behind `df->suspended` and instrumented silent) and
`target` (guarded). If both are clean and polling is still required, stub
`msm_devfreq_get_dev_status()` entirely and run the 100-cycle oracle: surviving
means `gpu_busy` is reached with `df->suspended` false -- a real finding about
the flag -- and dying means the offender is in the devfreq core's own work, not
in msm's callbacks, with the OPP/interconnect/regulator machinery inside
`target` the next suspects.
