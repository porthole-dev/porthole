---
id: venus-wedges-on-the-first-vbif-write
title: The msm8998 venus wedge is the first VBIF register write, and skipping the presets avoids it
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-08-29, kernel 7.2.2 #6/#7, node status=okay, netconsole VERIFIED end to end on usb0. Every arm from a fresh boot, with a control proving venus_core absent beforehand AND a readback of /sys/module/venus_core/parameters proving the knob was live. preset_limit=0 survives and probe fails cleanly -110; preset_limit=1 (writel 0x3 to base+0x80124) wedges; preset_limit=7 wedges. boot_stage=2 survives, 3 wedges. stop_at 6/9/10 survive, -1 wedges.
refutes: the wedge is inside core_power(POWER_ON); the wedge is the CPU_CS_SCIACMDARG0 poll after VIDC_CTRL_INIT; the missing interconnect vote is why; venus_boot/the trustzone PAS reset is what dies; a module parameter set on the modprobe command line overrides one in modprobe.d for the purpose of an experiment
first-learned: 2026-08-29
---

**The question** — enabling venus on msm8998 kills the SoC below printk. Where?

**The answer** — the **first write into the VBIF register block**:
`venus_set_registers()` writing `0x00000003` to `core->base + 0x80124`, which is
`VBIF_BASE_OFFS + 0x124`. One `writel`. `preset_limit=1` wedges; `preset_limit=0`
does not.

**And with all presets skipped, nothing wedges at all.** The probe runs to
completion and fails *honestly*:

    qcom-venus cc00000.video-codec: failed to reset venus core
    qcom-venus cc00000.video-codec: probe with driver qcom-venus failed with error -110

So `venus_boot_core()` polls `CPU_CS_SCIACMDARG0` a hundred times and times out
like it is supposed to. The firmware does not come up without the presets --
they are needed -- but as written they take the bus down first.

Everything else is exonerated, each on a controlled arm: the GDSC, all four
clocks (`clk_limit=4`), the interconnect votes, the trustzone PAS
authenticate-and-reset in `venus_boot()`, `venus_firmware_cfg()`,
`venus_set_hw_state_resume()`, and every read.

**Why the preset table is the suspect and not just the messenger** — msm8998 is
the odd one out. sdm660, the other 3XX part, presets three VBIF registers
(0x80010/0x80018/0x8001c). msm8998 presets seven, at completely different
offsets, plus one at 0xe2010 which is `WRAPPER_BASE + 0x2010` and not VBIF at
all. Whatever gates VBIF access on this SoC -- a clock, a subcore GDSC, an
ordering step -- mainline is not doing it before it writes there.

**Still open** — what makes VBIF reachable. Note `core_power_v1()` returns early
for msm8998 (`vcodec_pmdomains` is NULL) and so never calls
`vcodec_clks_enable()`, while `msm8998_res` does declare `vcodec0_clks`; and
`video_subcore0/1_gdsc` are HW_CTRL children of `video_top_gdsc` that nothing
explicitly powers. Both are worth trying before touching the table.

## The methodology is the load-bearing part

Three separate arms of this bisect produced three *different* confident and
*wrong* verdicts before the controls were tight enough. In order: "core_power
wedges", "the bus clock wedges", "the CPU_CS_SCIACMDARG0 poll wedges". Two
distinct contamination mechanisms:

1. **A stopped probe leaves the device bound.** `modprobe -r` then fails and the
   next `modprobe` returns `EBUSY` *without running any of the code under test*,
   which reads as "survived". Fix: reboot before every arm, and print a control
   proving the module is absent.

2. **`/etc/modprobe.d/00-venus-bringup.conf` carries `options venus_core
   stop_at=0`.** modprobe prepends config options to every load, so
   `modprobe venus_core boot_stage=7` becomes `stop_at=0 boot_stage=7` and the
   probe halts at checkpoint 1 -- the `boot_stage` code never executes. An
   entire ladder of "survived" results meant nothing. Fix: pass **every** knob
   explicitly (`stop_at=-1 boot_stage=-1 preset_limit=N`) and **read
   `/sys/module/venus_core/parameters/` back** as part of the arm.

That line was originally a no-op referencing a parameter that did not exist, and
became a live safety net the moment this series created it -- which is genuinely
useful for autoload, and genuinely a trap for experiments. Both are true; keep
it, and defeat it explicitly.

A no-op that looks like a pass will invent a root cause for you, and it will
sound convincing. Every row of evidence in this note has a reboot, an
absence control, and a parameter readback behind it.

**Also true**: `modprobe -r venus_core` after any stopped probe wedges the bus
the same way. Unloading is not a safe reset between experiments.
