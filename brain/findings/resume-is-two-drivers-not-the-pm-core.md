---
id: resume-is-two-drivers-not-the-pm-core
title: The slow wake is two drivers, not the PM core, and one of them is blocked by a 60-byte firmware file that declares no features
scope: device:google-taimen
subsystem: pm
severity: finding
confidence: proven
evidence: "taimen, kernel 7.2.2 #61 (aport r60), unplugged, driven over wifi with rtcwake -m mem. `echo 1 > /sys/power/pm_print_times` plus the power:suspend_resume ftrace event. Phase totals: dpm_resume 348.8 ms, dpm_suspend 309.2 ms, suspend_enter 148.0 ms, everything else under 25 ms. Per-callback: wiphy_resume [cfg80211] 312.8 ms, ftm4_resume [ftm4] 209.0 ms, ufshcd_system_resume 13.7 ms, arm_smmu_pm_resume 7.6 ms, 265 others summing ~33 ms. ftm4 A/B interleaved three rounds each way on one boot: keep_powered=0 -> 199.3/204.1/201.8 ms, keep_powered=1 -> 85.0/85.6/87.7 ms. firmware-5.bin for WCN3990 hw1.0 is 60 bytes and its IEs are 1 (FW_VERSION), 2 (TIMESTAMP), 5 (OTP_IMAGE), 6 (WMI_OP_VERSION) -- there is no IE 3 (FEATURES)."
refutes: "the slow wake is device PM in general; the slow wake is the compositor; ath10k has no suspend/resume support; a resume time can be attributed from PM phase totals alone; pm_print_times shows you every callback"
first-learned: 2026-09-19
---

**The question** — the phone takes ~2 s from power press to lit display, where
Android is instant. Where does it actually go?

**The answer** — **two drivers, 522 ms of a 576 ms device-resume budget.**
Everything else in the machine, 265 callbacks, is 54 ms together.

```
312.8 ms  ieee80211 phy0   wiphy_resume [cfg80211]
209.0 ms  ftm4 0-0049      ftm4_resume [ftm4]
 13.7 ms  ufshcd-qcom      ufshcd_system_resume
  7.6 ms  arm-smmu         arm_smmu_pm_resume
```

**ftm4: fixed sleeps that buy nothing.** `ftm4_resume()` called
`ftm4_power_up()` unconditionally -- 5 ms power settle, 10 ms reset assert,
**90 ms reset settle** -- then `ftm4_start()`. Downstream never pays that:
`fts_stop_device()` branches on `lowpower_mode` and that path leaves the rails
up, while `fts_start_device()` skips `board->power(info, true)` and calls
`fts_reinit()`, which is byte-for-byte what `ftm4_start()` already does. Adding
`ftm4.keep_powered` and measuring, interleaved, three rounds each way:

| | resume | suspend |
|---|---|---|
| keep_powered=0 | 199.3 / 204.1 / 201.8 ms | 1.6 / 1.3 / 2.4 ms |
| keep_powered=1 | **85.0 / 85.6 / 87.7 ms** | 0.2 / 0.2 / 0.3 ms |

**116 ms, 58%.** Slightly more than the 105 ms the sleeps predict, the balance
being the regulator enable itself.

**cfg80211: we disconnect from wifi on every single suspend.** Not a slow
driver -- `net/wireless/sysfs.c`:

```c
if (rdev->wiphy.wowlan_config) { ...; if (ret <= 0) goto out_unlock_rtnl; }
/* Driver refused to configure wowlan (ret = 1) or no wowlan */
cfg80211_leave_all(rdev);
```

With no `wowlan_config` every interface is torn down, and resume rebuilds the
stack (the 313 ms) and then re-authenticates from scratch -- a further **2.6 s**
before `wlan0: associated` in dmesg. ath10k *does* implement
`ath10k_wow_op_suspend`/`_resume`; the gate is upstream of them.
`ath10k_wow_init()` returns early unless `ATH10K_FW_FEATURE_WOWLAN_SUPPORT`
(bit 6) is set, and that comes from the firmware file's FEATURES IE.
`/lib/firmware/ath10k/WCN3990/hw1.0/firmware-5.bin` is **60 bytes** and carries
IEs 1, 2, 5 and 6 only -- **there is no IE 3, so no feature bit is set at all.**
WCN3990's real firmware arrives over QMI; this file is metadata, and the
metadata does not claim WoWLAN.

**What this rules out** — "device PM is slow" (it is 54 ms once these two are
removed), "it is the compositor" (the compositor is on top of a 740-1190 ms
kernel path, not the cause of it), and "ath10k cannot suspend". It also kills
attributing resume from the ftrace phase totals alone: `dpm_resume` is 348.8 ms
while these two callbacks sum to 522 ms, because `pm_async` runs them in
parallel and the phase is the critical path, not the sum.

**Two instrument traps, both of which cost a run here.**

1. **`pm_print_times` output does not parse the way it looks.** A callback in a
   module prints `call ... wiphy_resume [cfg80211] returned 0 after N usecs`,
   so a pattern anchored on `(\S+) returned` silently drops exactly the
   module-provided callbacks -- which are the interesting ones. Both of the two
   biggest costs in this machine were invisible in the first ranking, and the
   totals looked like "device PM is only 43 ms".
2. **`pm_print_times` overflows the printk ring across repeated cycles.** An
   A/B of four suspends kept only the last one; `dmesg -C` before each cycle and
   dump after it, or three arms of four silently vanish.

**Still open** — whether the running WCN3990 firmware actually advertises
`WMI_SERVICE_WOW`. `ath10k_wow_init()` `WARN_ON`s if the feature bit is set but
the service is not, so the bit cannot simply be added blind. Reading the service
map needs `CONFIG_ATH10K_DEBUGFS`, which is `is not set` in this config -- one
config line and a `fast` build away. See
[[the-skin-ladder-caps-the-die-22c-and-is-self-limiting]] for the other half of
this session.
