---
id: mainline-msm8998-has-no-rpm-sleep-handshake
title: The SoC can never reach VDD-min because mainline msm8998 has no RPM sleep-set handshake, not because something is voting against it
scope: soc:msm8998
subsystem: pm
severity: finding
confidence: proven
evidence: "taimen, kernel 7.2.2 #65, 11 successful s2idle cycles this boot, driven by tools/ph-suspend-cycle.sh on the logind path. /sys/kernel/debug/qcom_stats/{vlow,vmin} read Count: 0 throughout. The record decode is sound: qcom_stats names its debugfs files from the record's stat_type field and produced the real 4-char types 'vlow' and 'vmin', so the base offset is right and Count: 0 is genuine. Sleep-set interconnect votes enumerated from interconnect_summary: on RPM, tag 1 = RPM_ACTIVE_TAG, tag 2 = RPM_SLEEP_TAG, tag 3 = RPM_ALWAYS_TAG (include/dt-bindings/interconnect/qcom,rpm-icc.h). Only two devices hold tag-3 votes -- a8f8800.usb (240000 avg / 700000 peak to slv_ebi) and cc00000.video-codec (2500 avg). dwc3_qcom_suspend() calls dwc3_qcom_interconnect_disable(), so USB drops its vote during suspend. Unbinding venus zeroed its vote (verified in interconnect_summary); a suspend with system-pc enabled AND venus unbound still gave vlow Count: 0. Vendor side, drivers/cpuidle/lpm-levels.c: a level with qcom,notify-rpm runs msm_spm_config_low_power_mode(ops->spm, mode, notify_rpm), msm_rpm_enter_sleep(0, cpumask) and msm_mpm_enter_sleep(us, from_idle, cpumask) BEFORE calling PSCI. Mainline side: `grep -rn 'rpm_enter_sleep|enter_sleep|SLEEP_SET|sleep_set' drivers/soc/qcom/ drivers/interconnect/qcom/ drivers/clk/qcom/clk-smd-rpm.c` returns nothing, and drivers/soc/qcom/spm.c contains zero occurrences of 'notify'. qcom_spm binds only 17812000.power-manager and 17912000.power-manager, the two L2 SAWs; msm8998.dtsi has no CCI/system SAW node, while the vendor's system cluster is qcom,spm-device-names = \"cci\". qcom_mpm IS present and bound."
refutes: "something is voting against VDD-min and finding the voter will fix it; the GPU's 3.3 GB/s interconnect vote blocks suspend; the bwmon's 1.1 GB/s vote blocks suspend; vlow Client Votes names a blocker worth decoding; enabling system-pc is what stands between this port and vendor idle power; system-pc hangs s2idle on this device"
first-learned: 2026-09-19
---

**The question** — `qcom_stats` has read `vmin Count: 0` and `vlow Count: 0`
since the port began. Who is blocking VDD-min?

**The answer — nobody. There is no handshake to block.** Mainline msm8998 has
no code that tells the RPM to swap to its sleep set, and no system-level SPM to
carry the hardware signal that would do it implicitly.

The vendor's `lpm-levels` does three things before PSCI, for any level marked
`qcom,notify-rpm` -- which on msm8998 is exactly one level, `system-pc`:

```c
msm_spm_config_low_power_mode(ops->spm, mode, notify_rpm);  /* arm the SAW handshake */
msm_rpm_enter_sleep(0, cpumask);                            /* swap RPM to the SLEEP set */
msm_mpm_enter_sleep(us, from_idle, cpumask);                /* MPM wake pins + timer */
```

Mainline has **none** of the first two. `spm.c` contains zero occurrences of
`notify`, and nothing anywhere in `drivers/soc/qcom`,
`drivers/interconnect/qcom` or `clk-smd-rpm.c` mentions an RPM sleep-set
transition. `qcom_spm` binds only the two **L2** SAWs (`17812000`, `17912000`);
the vendor's system cluster drives a **CCI** SPM that has no node in
`msm8998.dtsi` at all. `qcom_mpm` is present and bound, so of the three, only
the MPM exists.

The RPM has the sleep-set *values* -- `icc-rpm` sends `RPM_SLEEP_TAG` requests
-- it is never told to *use* them.

**Two things this kills, so nobody spends a session on them.**

1. **`vlow Client Votes` is not a lead.** It read `0x83818381` before one
   suspend and `0x81838183` after -- the same bytes rotated by one. Decoding
   it would name nothing, because the mode is never attempted.
2. **The big interconnect votes are active-only.** `mas_oxili` (GPU,
   3 296 000) and `1008000.pmu` (the CPU/DDR bwmon, 1 144 000) look alarming
   in `interconnect_summary` at idle and are both **tag 1 = RPM_ACTIVE_TAG**,
   which never enters the sleep set. Read the tag column before chasing a
   number. The only tag-3 votes on this device are USB (dropped inside
   `dwc3_qcom_suspend()`) and venus (`pm_runtime_forbid()`, so it never
   drops) -- and zeroing venus by unbinding it changed nothing.

**RETRACTED 2026-09-19, same evening: `system-pc` is NOT safe to enable.**
This note first said it no longer hangs, on the strength of two clean cycles.
A third, on the same kernel after a reboot, **did not come back**: no USB
enumeration, no fastboot, nothing on wifi, for over five minutes, with a 30 s
RTC alarm armed. It took a power-key press. Two successes are not a result on
a device whose oldest trap is that a booted phone and a hung one look
identical. Whether that third cycle was a real collapse with no armed wake or
an ordinary hang cannot be told apart from the host, which is exactly why n=2
was not enough. Treat `system-pc` as **unproven and hands-required** until
someone runs it with the panel-photo or the pstore route armed.

**What did change: `system-pc` reached a state the earlier kernel could not.** The 6.18-era result that
cpuidle retention and power-collapse hang s2idle
(`docs/campaign/ws-05-suspend.md`) does not hold on 7.2. States 1 and 2 are
enabled by default with millions of uses and 11/11 suspends are clean, and
writing `0` to every `cpuidle/state3/disable` gave two clean suspends that
resumed on the RTC with `boot_id` unchanged -- and then a third that never
returned (see the retraction above). The `10-taimen-cpuidle` hook that
was written to disable states 1-2 across suspend is **not installed** -- the
device APKBUILD still carries its comment with no `install` line under it --
and on 7.2 it is not needed.

**But do not enable `system-pc` as a shipped default.** With it enabled,
`state3/s2idle/usage` moved 357 467 -> 772 698 across a single 30 s suspend:
about 14 000 entries per second. PSCI is returning from it almost immediately,
because nothing armed the handshake it exists to trigger. It buys no power and
spins the CPU.

**What parity actually needs**, in dependency order: a CCI/system SAW node and
`spm.c` support for it including the notify-RPM bit; something to issue the RPM
sleep-set swap at system-pc entry; and the MPM wake programming
(`msm_mpm_enter_sleep`'s timed-wake path) hooked to that entry. That is an
unimplemented subsystem, not a tweak -- and it is the whole of the BP-13/BP-14
prize.

**Trap collected on the way:** venus cannot be rebound after `unbind`. The
probe fails `-110` with a WARN and only a reboot brings it back.
