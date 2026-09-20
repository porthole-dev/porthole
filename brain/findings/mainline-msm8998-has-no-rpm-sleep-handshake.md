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

**A retraction of my own retraction, same evening.** This note briefly said
`system-pc` was proven unsafe, because a third cycle went dark for 45 minutes
and needed a power-key press. That attribution was wrong and is withdrawn: the
harness never ran that cycle. The suspend log held no `TRY` line for that boot,
`state3/disable` read 1 and `state3/s2idle/usage` read 0, so `system-pc` was
never entered. The journal shows what actually happened -- `Reached target
Sleep` at 22:22:59 and `PM: suspend entry (s2idle)` at 22:23:00, a 29-minute
gap, then `PM: suspend exit` at 22:51:47. **The phone idle-suspended by
itself** while nobody was driving it, and stayed down because nothing had
scheduled a wake.

So the standing evidence is two clean `system-pc` cycles and no third data
point. That is **unproven, not disproven** -- and two cycles is still not a
result on a device where a booted phone and a hung one look identical. Arm
`tools/ph-afk.sh` before any unattended run, or the phone will sleep under the
experiment and the null will be about nothing.

**What did change: `system-pc` reached a state the earlier kernel could not.** The 6.18-era result that
cpuidle retention and power-collapse hang s2idle
(`docs/campaign/ws-05-suspend.md`) does not hold on 7.2. States 1 and 2 are
enabled by default with millions of uses and 11/11 suspends are clean, and
writing `0` to every `cpuidle/state3/disable` gave two clean suspends that
resumed on the RTC with `boot_id` unchanged. A third attempt never ran; see
above. The `10-taimen-cpuidle` hook that
was written to disable states 1-2 across suspend is **not installed** -- the
device APKBUILD still carries its comment with no `install` line under it --
and on 7.2 it is not needed.

**But do not enable `system-pc` as a shipped default.** With it enabled,
`state3/s2idle/usage` moved 357 467 -> 772 698 across a single 30 s suspend:
about 14 000 entries per second. PSCI is returning from it almost immediately,
because nothing armed the handshake it exists to trigger. It buys no power and
spins the CPU.

**Measured 2026-09-19, late: what `system-pc` actually does.** Three clean
cycles now, all returning on the RTC. With it enabled on all 8 CPUs, against a
control that is the same 31 s suspend with it disabled:

| | `arch_timer` IRQs | `system-pc` entries |
|---|---|---|
| control, `system-pc` off | 1 742 | 0 |
| `system-pc` on | **140 299** | **386 598** |

`CONFIG_HZ=1000`, so the control's 1 742 ticks over 31 s across 8 CPUs (~56/s)
is a properly tickless idle. Enabling `system-pc` multiplies the local-timer
rate **80x**. `state3/s2idle/time` is 9 344 590 us over 386 598 entries --
**24 us per entry**. That is the round trip of an SMC that does nothing:
**the firmware accepts the call and returns immediately without collapsing.**

The tick storm is the *symptom*, not the cause: each enter/exit pair re-arms
the tick that `local-timer-stop` had just handed to the broadcast device. But
it is self-reinforcing, and it matters for PC-mode firmware, where the system
only collapses when the **last** CPU enters at affinity 2. Eight CPUs bouncing
on their own ticks never satisfy that condition.

`state3/rejected` is 4 724 against 386 598 entries, so outright rejection is
not the main path -- the calls are being accepted and doing nothing.

**CORRECTED 2026-09-20, by reading the code instead of the counters.** This
note previously said glink-rpm's ~237 interrupts per suspend were wakes and
that masking the RPM channel was the next port. Both were wrong, and the patch
built on them (0248) has been reverted.

Tracing `events/irq/irq_handler_entry` together with
`events/power/suspend_resume` and counting only what falls between
`machine_suspend` begin and end -- the window where the trace clock is frozen,
so anything firing during the sleep lands inside it -- gives **8 IPIs and 2
arch_timer, and zero glink-rpm**. All 247 glink-rpm interrupts are outside it:
RPM voting traffic in the suspend and resume device phases, which is what the
channel exists for. The measurement after the patch agreed that it changed
nothing: 237 -> 358, noise. **s2idle on this device already sleeps cleanly.
There are essentially no spurious wakes to remove.**

**And the OSI story is not what it looked like.** `psci_dt_cpu_init_topology()`
only installs `psci_enter_s2idle_domain_idle_state` under
`psci_has_osi_support()`, and our CPU nodes carry `power-domain-names = "cprh"`
so `dt_idle_attach_cpu(cpu, "psci")` returns NULL and that path bails. The
reason `system-pc` still runs under s2idle is generic, in
`drivers/cpuidle/dt_idle_states.c:38`:

```c
/* ... So enter() can be also enter_s2idle() callback. */
idle_state->enter_s2idle = match_id->data;
```

Every DT idle state gets `enter_s2idle` set to the same callback as `enter`.
So `system-pc` is entered through plain `psci_enter_idle_state()`, which hands
`0x42000343` to the firmware via `CPU_PM_CPU_IDLE_ENTER_PARAM_RCU` with **no
domain coordination at all**. There is no genpd, no OSI, and none is needed
for the call to happen.

**Which puts the limit in the firmware.** The boot log is explicit:

```
psci: OSI mode supported.
psci: [Firmware Bug]: failed to set PC mode: -3
```

The TZ advertises OS_INITIATED in its CPU_SUSPEND feature bits and then
rejects `SET_SUSPEND_MODE` in both directions. In Platform-Coordinated mode
the firmware, not Linux, decides the cluster and system state from what the
cores request, and it is free to clamp an affinity-2 request down to a CPU
collapse. That is what the numbers say it does: 386 598 entries, 4 724
rejected outright, the rest returning success in an average of 24 us.

So there is no missing AP-side call to port. `msm_rpm_enter_sleep()`'s
remaining half is a channel mask that measurably does nothing here, and the
MPM handover already runs. What is left is a firmware that will not perform a
system collapse, on a SoC whose PSCI implementation is already known buggy.

**What parity actually needs**, in dependency order: a CCI/system SAW node and
`spm.c` support for it including the notify-RPM bit; something to issue the RPM
sleep-set swap at system-pc entry; and the MPM wake programming
(`msm_mpm_enter_sleep`'s timed-wake path) hooked to that entry. That is an
unimplemented subsystem, not a tweak -- and it is the whole of the BP-13/BP-14
prize.

**Trap collected on the way:** venus cannot be rebound after `unbind`. The
probe fails `-110` with a WARN and only a reboot brings it back.
