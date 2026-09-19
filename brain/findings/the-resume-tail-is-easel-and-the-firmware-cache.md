---
id: the-resume-tail-is-easel-and-the-firmware-cache
title: The two unattributed halves of the s2idle cycle are one PM notifier each -- Easel's PCIe revival and the firmware cache
scope: device:google-taimen
subsystem: pm
severity: finding
confidence: proven
evidence: "taimen, kernel 7.2.2 #64 (aport r63) then #65 (r64), on USB, one s2idle cycle per arm driven by tools/ph-suspend-cycle.sh. Instrument: events/notifier/notifier_run (prints each PM notifier callback by %ps), events/power/suspend_resume and events/power/device_pm_callback_{start,end}. Per-notifier duration = delta from one notifier_run to the next, the tracepoint being emitted before the call. BEFORE: window between sync_filesystems end (2177.604954) and freeze_processes begin (2177.721481) = 116.5 ms, of which fw_pm_notify 110.9 ms; window after thaw_processes end (2178.237140) = 353.9 ms, of which easel_pm_notify 328.5 ms and pci_notify 23.4 ms. pm_prepare_console measured at 0.004 ms. AFTER (CONFIG_FW_CACHE=n + async Easel revival): window A ~5 ms with fw_pm_notify absent from kallsyms, window B 0.3 ms with easel_pm_notify 0.057 ms. End to end from the device's own dmesg, PM: suspend entry -> PM: suspend exit: 440.5 ms and 406.0 ms, against 958 ms before. Restarting tasks: Done -> PM: suspend exit: 350 ms -> 17.8 ms."
refutes: "the ~128 ms before freeze_processes is pm_prepare_console; the ~350 ms after Restarting tasks is pm_restore_console; the heavy PM notifiers on this device are cpufreq, zram/zsmalloc, the three remoteprocs or ath10k; dpm_suspend traced at 309 ms is a capture artefact; you need CONFIG_FUNCTION_GRAPH_TRACER to attribute a notifier chain"
first-learned: 2026-09-19
---

**The question** — ~480 ms of a 958 ms s2idle cycle sat outside device PM, in
two blocks nobody had attributed: one inside `suspend_enter` before
`freeze_processes`, one inside `suspend_finish` after `Restarting tasks: Done`.

**You do not need a function tracer.** This kernel has none --
`available_tracers` is `nop`, `CONFIG_FUNCTION_TRACER` is unset -- so the
`function_graph` recipe cannot run at all. It is also not needed:
`events/notifier/notifier_run` is a plain tracepoint, present whenever
`CONFIG_TRACING=y`, and its `TP_printk` is `"%ps"` on the callback. Enable it
with `power/suspend_resume` for the phase markers and every PM notifier names
itself, in order, with a timestamp.

**Each window is one notifier.**

```
window A   116.5 ms   fw_pm_notify      110.9 ms     pm_prepare_console  0.004 ms
window B   353.9 ms   easel_pm_notify   328.5 ms     pci_notify         23.4 ms
```

`fw_pm_notify` -> `device_cache_fw_images()`, and this device sets
`CONFIG_FW_LOADER_COMPRESS_ZSTD=y`, so every suspend zstd-decompresses the
firmware of every device into RAM -- to be dropped again 10 s after resume.
The cache exists so a firmware request during resume can be served without the
filesystem. Nothing on this device makes one: rootfs is UFS and is back before
driver resume. `CONFIG_FW_CACHE=n` removes the notifier outright.

`easel_pm_notify` on `PM_POST_SUSPEND` powers Easel on, cycles PERST#,
retrains the PCIe link, polls config space in `msleep(20)` steps and rescans
the bus. The `PM_POST_SUSPEND` chain runs inside `suspend_finish()`, **before
`pm_suspend()` returns** -- so all 328 ms are in front of the user. Moving it
to a work item costs nothing: while it runs Easel is absent, which is exactly
the state a *failed* revival already leaves behind, so no caller learns a new
case. `PM_SUSPEND_PREPARE` `cancel_work_sync()`s first.

**Together: 958 ms -> 440 ms, measured end to end from dmesg.**

**The 309 ms `dpm_suspend` was real, not a capture artefact.** It is
`wiphy_suspend`, and it reads 246 ms or 113 ms depending on one thing: whether
WoWLAN was armed. The system-sleep hook only runs on the logind path, so any
cycle driven by `rtcwake` or a raw `/sys/power/state` write measures the
unarmed number -- unless an earlier logind suspend armed it, because the hook
deliberately leaves it armed. Two runs on the same kernel differ by 240 ms for
that reason alone. Always record `iw phy0 wowlan show` in the same capture.
