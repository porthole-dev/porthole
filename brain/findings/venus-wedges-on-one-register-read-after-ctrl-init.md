---
id: venus-wedges-on-one-register-read-after-ctrl-init
title: The msm8998 venus wedge is one register read after VIDC_CTRL_INIT, not the power sequence
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-08-29, kernel 7.2.2 #5/#6, node status=okay, netconsole VERIFIED end to end on usb0 before each arm. Every arm preceded by a reboot and a control proving venus_core was absent. stop_at ladder 5/6/9/10 all survive, -1 wedges; boot_stage 5/6/7 all survive. Wedge = marker on netconsole then nothing at all, phone returns on the hardware watchdog.
refutes: the wedge is inside core_power(POWER_ON); the wedge is the first venus register touch; the missing interconnect vote is why; the CPU cannot reach the venus register block; venus_boot/the trustzone PAS reset is what dies; a load/unload cycle is a valid substitute for a reboot between arms
first-learned: 2026-08-29
---

**The question** — enabling venus on msm8998 kills the SoC below printk.
[[venus-dies-below-printk-on-msm8998]] placed it "inside `core_power(POWER_ON)`
or the first register touch after it". Which is it?

**The answer** — neither. Instrument by *declining to execute* rather than by
printing (patches 0181/0183/0184 add `stop_at`, `clk_limit`, `boot_stage`), and
the ladder is unambiguous:

| arm | what ran | result |
|---|---|---|
| `stop_at=6` | GDSC + all four clocks | survives |
| `stop_at=9` | + `hfi_core_resume(false)`, `venus_firmware_init`, `venus_boot()` | survives |
| `stop_at=10` | + `venus_firmware_cfg()` | survives |
| `boot_stage=6` | + every CPU **write** into venus register space | survives |
| `boot_stage=7` | + `VIDC_CTRL_INIT` — the firmware **starts** | survives |
| `stop_at=-1` | + `readl(cpu_cs_base + CPU_CS_SCIACMDARG0)` | **wedges** |

So the power sequence is entirely innocent. `core_power_v1()` on this SoC is
only `clk_prepare_enable()` of four clocks -- `vcodec_domains_enable()` returns
immediately because `msm8998_res` declares no `vcodec_pmdomains`, and the
`clk_set_rate()` is gated to V6/V4-lite -- and `clk_limit=4` proves all four
enable fine. `VIDEO_TOP_GDSC` is already up by then, powered by genpd during the
`pm_runtime_get_sync()` that reaches `venus_runtime_resume()`. The interconnect
votes are placed. The trustzone PAS authenticate-and-reset in `venus_boot()`
succeeds. Every CPU write lands.

**What kills it is a single read**: `CPU_CS_SCIACMDARG0` (0x4c), polled
immediately after `VIDC_CTRL_INIT` (0x48) starts the firmware. Adjacent
registers in the same block, so this is not a wrong base offset -- and
`venus_hwversion()` reads `wrapper_base` a moment later without harm. A posted
write to a non-responding target is dropped; a read must return data, and this
one never does.

**With that one poll skipped, venus comes up.** `boot_stage=7` leaves
`venus_core` **bound** to `cc00000.video-codec`, and `venus_dec`/`venus_enc`
then load without wedging. That is much further than this port has ever got.

**Still open** — no V4L2 decoder node appears. The live devicetree has the
`video-decoder`/`video-encoder` children with the right compatibles, and the
`qcom-venus-decoder`/`qcom-venus-encoder` drivers are registered, but
`of_platform_populate()` created no child devices, so nothing binds. That, and
*why* the read hangs, are the next two questions. Skipping a readiness poll is a
bisect result, not a fix.

**Also true**: `modprobe -r venus_core` after any stopped probe wedges the bus
the same way. Unloading is not a safe way to reset between experiments.

**The method matters more than the result.** The first two runs of this bisect
produced two *different* confident verdicts, both wrong ("core_power wedges",
then "the bus clock wedges"). Cause: a stopped probe leaves the device bound,
`modprobe -r` then fails, and the next `modprobe` returns `EBUSY` **without
running any of the code under test** -- which reads as "survived". A no-op that
looks like a pass will invent a root cause for you.

Reboot before every arm, and print a control proving the module is absent
first. Every row above has one.
