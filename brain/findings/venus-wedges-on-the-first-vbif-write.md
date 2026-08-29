---
id: venus-wedges-on-the-first-vbif-write
title: msm8998 TZ refuses venus resume with -EINVAL, mainline swallows it, and the whole block stays dark
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-08-29, kernel 7.2.2 #6/#7, node status=okay, netconsole VERIFIED end to end on usb0. Every arm from a fresh boot, with a control proving venus_core absent beforehand AND a readback of /sys/module/venus_core/parameters proving the knob was live. preset_limit=0 survives and probe fails cleanly -110; preset_limit=1 (writel 0x3 to base+0x80124) wedges; preset_limit=7 wedges. boot_stage=2 survives, 3 wedges. stop_at 6/9/10 survive, -1 wedges.
refutes: the wedge is inside core_power(POWER_ON); the wedge is the CPU_CS_SCIACMDARG0 poll after VIDC_CTRL_INIT; the missing interconnect vote is why; venus_boot/the trustzone PAS reset is what dies; the missing content-protection regions are why TZ refuses; releasing the venus CPU with venus_reset_cpu() is a usable fallback; a module parameter on the modprobe command line overrides one in modprobe.d for the purpose of an experiment
first-learned: 2026-08-29
---

**The root cause, measured** — the trustzone refuses to resume venus, and
mainline maps that refusal to success:

    qcom-venus cc00000.video-codec: venus: scm_set_remote_state(resume=1) = -22
    qcom-venus cc00000.video-codec: venus: scm_set_remote_state(resume=0) = 0

-22 is -EINVAL, and `venus_set_hw_state()` does `if (resume && ret == -EINVAL)
ret = 0;`. So the venus CPU is never released, nothing anywhere says so, and
**the entire venus register space stays unreachable** -- which is why the first
write into it takes the NoC down. Note the *suspend* direction returns 0, so SCM
itself works; TZ specifically rejects the resume.

Everything below was the route to that, and each step is still true.

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

**Nine hypotheses refuted, each with its own control.** In order tried:
the missing interconnect vote; core_power/the four clocks; the CPU access path
and the CPU_CS_SCIACMDARG0 poll; the swallowed TZ -EINVAL; releasing the CPU
with venus_reset_cpu(); the content-protection regions; the two MMSS NoC clocks
(gcc_mmss_sys_noc_axi_clk really was off, and voting it really did change the
state -- and did not fix the wedge); the vendor's preset ORDERING, with the
presets written before venus_boot(); the vcodec subcore power domains (verified
active in SW mode at the wedge point, with all six clocks on); and rating the
core clocks, which the vendor does via __scale_clocks() and mainline skips on
3XX. **The preset table itself matches the vendor byte for byte.**

**The one remaining difference** in the vendor's `__venus_power_on()` that
mainline does not do at all: `__alloc_imem(device, res->imem_size)`, with
`qcom,imem-size = <524288>` in msm8998-vidc.dtsi, called BEFORE
`__set_registers()`. `msm8998_res` has `vmem_id = VIDC_RESOURCE_NONE,
vmem_size = 0`. That is the next thing to try, and it is now the only
untried step in the sequence.

**Two earlier fixes tried and refuted, each with its own control**

- *"Release the CPU ourselves when TZ refuses."* `venus_reset_cpu()`, the
  no-TZ path's action, **wedges the bus too**. The wrapper registers it writes
  (FW/CPA/NONPIX windows, A9SS reset) are as unreachable as VBIF. There is no
  reaching venus from the CPU while TZ holds it.
- *"The content protection regions are missing, so TZ refuses."* `venus_boot()`
  only calls `qcom_scm_mem_protect_video_var()` when `res->cp_size` is set, and
  msm8998 set none of the `cp_*` fields. Filling them in from the downstream
  context banks -- `cp_size` = `venus_ns/virtual-addr-pool[0]` = 0x70800000,
  nonpixel = <0x1000000 0x24800000>, the same four values sdm845 carries --
  changes TZ's answer **not at all**. Still -22. Correct completeness fix, wrong
  culprit; kept for the same reason the interconnect vote was.

**Still open** — why TZ rejects the resume. Worth trying next: a remote-state
id other than 0, and forcing `use_tz = false` so venus takes the
`venus_boot_no_tz()` path entirely rather than PAS -- though that needs the
firmware carveout to be reachable without PAS, which is the thing to check
first.

**Also still open** — what makes VBIF reachable. Note `core_power_v1()` returns early
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
