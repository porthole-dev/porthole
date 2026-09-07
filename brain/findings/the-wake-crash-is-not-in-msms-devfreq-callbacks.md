---
id: the-wake-crash-is-not-in-msms-devfreq-callbacks
title: The display-wake crash is not in any of msm's devfreq callbacks -- but it is specific to the GPU's devfreq
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
refutes: "that the offending work is in msm_devfreq_get_dev_status, msm_devfreq_target or msm_devfreq_get_cur_freq; that any periodic kernel wakeup during the display transition would do; that VDD_MX has anything to do with it"
evidence: "taimen, tools/ph-wake-cycle.py, 2026-08-27, one arm per line, gpu=suspended before every press. r89 aport baseline died at 45. get_dev_status stubbed to constants: 19. plus target stubbed to `return 0` (8 bytes compiled): 48. plus get_cur_freq stubbed: 14. Stub sizes verified with nm on the loaded .ko. With /sys/class/devfreq/5000000.gpu/polling_interval = 0 and NOTHING else changed: survived 100 transitions, 0 presses missed. Throughout that immune run 1da4000.ufshc devfreq was still polling at 60 ms."
first-learned: 2026-08-27
---

**The question** — the crash needs devfreq to be polling. Which of msm's
devfreq callbacks is doing the damage?

**None of them.** All three were stubbed, one at a time, cumulatively:

    arm                                          died at
    r89 aport baseline                            45
    get_dev_status -> constants                   19
      + target -> return 0                        48
      + get_cur_freq -> constant                  14
    polling_interval = 0, nothing else changed    100 (survived)

With all three inert the poll enters no msm code at all -- no `df->lock`, no
ktime, no `gpu_busy()`, no OPP lookup, no `dev_pm_opp_set_rate()`, and no
`clk_get_rate()`. The crash rate is unchanged. The stubs were verified on the
loaded module, not assumed: `nm` reports `msm_devfreq_target` at **8 bytes**
and `msm_devfreq_get_dev_status` at 44.

**The polling arm is the control that makes the rest mean anything.** It is the
same instrument, the same session and the same kernel, and it separates immune
(100) from not (14-48) cleanly. Anything in that 14-48 band is one draw from
the same distribution; do not read a trend into it.

**One callback nobody had removed.** `devfreq_set_target()` calls
`->get_cur_freq` UNCONDITIONALLY on every poll, before any `df->suspended`
check of ours can matter, and `get_freq()` falls through to
`clk_get_rate(gpu->core_clk)` on a5xx, which has no `gpu_get_freq`. The earlier
experiment recorded as "removed get_freq" only removed it from
`get_dev_status`. So a clk read against a suspended GPU was still happening on
every poll in every previous arm. It is gone now, and it was not the cause.

**It is still specific to the GPU's devfreq.** This comes free from the immune
arm: `1da4000.ufshc` has its own devfreq monitor and it was polling every 60 ms
throughout those 100 clean cycles. So "a devfreq monitor work item running" and
"a CPU woken every ~50 ms" are both insufficient. Only the GPU's poll matters.

> **SUPERSEDED, 2026-08-27 (evening).** Everything above this line stands --
> msm's own devfreq callbacks really are not the site. The paragraph below does
> NOT: it concludes the cause is inside the devfreq core "rather than any
> register access", and the answer turned out to be a GPU register access after
> all, in `a5xx_hw_init()`, reached when a runtime power collapse is too short
> to discharge GX. The two sibling notes carry the same correction; this one
> was missed, so a reader following the link from either of them landed on a
> dead end with no sign it was one.
> See [[a-short-power-collapse-leaves-the-a5xx-cp-alive]].

**What is left.** The devfreq core's own periodic path for THIS device, with
every msm callback inert: the `devfreq_monitor` delayed work, `devfreq->lock`
-- which `msm_devfreq_suspend()`, `msm_devfreq_resume()`, `msm_devfreq_idle()`
and `msm_devfreq_active()` all take as well -- the governor's arithmetic,
`devfreq_update_status()`, and the transition notifier chain. The shape that
fits is a race or a lock interaction between the monitor work and the GPU's own
resume path, which is exactly what a display wake drives, rather than any
register access. msm's own `idle_work`/`boost_work` delayed works are also worth
eliminating: they are GPU-specific and are not what `polling_interval` gates.

Related: [[holding-vdd-mx-does-not-stop-the-wake-crash]],
[[the-wake-crash-is-devfreq-not-a-register-access]],
[[a-short-power-collapse-leaves-the-a5xx-cp-alive]].
