---
id: holding-vdd-mx-does-not-stop-the-wake-crash
title: Holding VDD_MX does not stop the display-wake crash -- neither enabled nor at TURBO
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
refutes: "that the display-on arm of the wake crash is immune because the DPU holds VDD_MX at 384; that the fault is an access into a block whose MX has fallen to level 0 while GX is still reachable; that giving the GPU's power domains a single owner fixes it"
evidence: "taimen, tools/ph-wake-cycle.py, 2026-08-27. Three arms, same instrument, same 2 s spacing, gpu=suspended before every press. r89 baseline: died at cycle 45. r92 (gpucc in VDD_MX, so mx -> gpu_cx -> gpu_gx and MX is never collapsed): died at 22, with the genpd summary reading `mx on 0` while blanked instead of `off-0`. r93 (same, plus required-opps = <&rpmpd_opp_turbo> on gpucc): died at 14, with `mx on 384` VERIFIED while the panel was disabled and the GPU suspended."
first-learned: 2026-08-27
---

**The question** — [[the-wake-crash-is-devfreq-not-a-register-access]] concluded
that the three immune arms have exactly one thing in common, VDD_MX staying
voted, and that the fault is a transaction into a block whose MX has dropped to
level 0 while GX is still up. It proposed giving the GPU's split power domains
a single owner. Does holding MX actually stop the crash?

**No.** Two arms, both measured with the counting oracle against a baseline
that died at cycle 45:

    arm                                        mx while blanked   died at
    r89 baseline                               off-0               45
    gpucc placed in VDD_MX                     on 0                22
    the same, plus required-opps = turbo       on 384              14

The third arm is the decisive one. 384 is exactly the level the DPU holds MX at
when the display is on -- the state credited with surviving 118 cycles -- and
holding it with the display OFF confers no immunity at all. The control was
read off the device in that state, not assumed: panel `disabled`, GPU
`suspended`, `mx on 384`, `gpu_gx off-0`.

**So the MX correlation was a coincidence of the three arms.** All of "GPU
pinned on", "devfreq polling off" and "display kept on" do keep MX voted, and
none of them is immune *because* of it.

**What this leaves.** Re-read the display-on arm carefully: swipes kept the
session awake, the GPU still suspended and resumed 118 times, and what never
happened was the **panel blank/unblank transition itself**. That, not the rail,
is the condition the arm actually held constant. The next suspect is the
display side of that transition -- the DPU/MDSS domain, the DSI and its clocks,
or the interconnect path -- not the GPU's power domains.

**Do not spend time on** the multi-domain rework the previous note proposed
(`dev_pm_domain_attach_by_name` for "gx"/"mx" on the GPU node, following the
a6xx GMU and `remoteproc@4080000`). Its whole purpose was to stop MX dropping
under a live GX, and that condition has now been created deliberately, twice,
without helping. It also costs the `opp-level` MX voting on the GPU's own OPP
table, which is real.

**A side fact worth having** — `use_rpm` on a qcom clock controller does NOT
let its power-domain vote drop. `clk_core_prepare()` takes a runtime PM
reference on the provider device when a clock's `prepare_count` goes 0 -> 1 and
returns it only on unprepare, so one permanently-prepared clock pins the
controller runtime-active for the life of the system. On taimen the gpucc
device read `active` in every sample. Placing a clock controller in a domain
therefore votes that domain permanently, whatever `use_rpm` says.

Related: [[the-wake-crash-is-devfreq-not-a-register-access]].
