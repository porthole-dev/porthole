---
id: venus-dies-below-printk-on-msm8998
title: Enabling venus on msm8998 kills the SoC instantly, and the missing bus vote is not why
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-08-28, kernel #100. Marker to /dev/kmsg one line before `modprobe venus_core`, netconsole verified end to end on usb0+wlan0 immediately beforehand. Log carries the marker and then nothing at all; phone returns with androidboot.bootreason=watchdog. Repeated with patch 0189 (interconnects wired) live in the devicetree -- byte-identical outcome.
refutes: venus on msm8998 only needs CONFIG_VIDEO_QCOM_VENUS plus the firmware; the hang is the missing interconnect vote; netconsole can observe a boot-time module probe
first-learned: 2026-08-28
---

**SUPERSEDED IN PART, 2026-08-29** — the localisation below ("inside
`core_power(POWER_ON)` or the first register touch after it") is now measured
WRONG. The power sequence, all four clocks, the trustzone PAS reset and every
CPU write into venus all survive; the wedge is one `readl` of
`CPU_CS_SCIACMDARG0` after `VIDC_CTRL_INIT`. See
[[venus-wedges-on-the-first-vbif-write]]. Everything else here --
the silence, the watchdog recovery, the bootloop risk, the blacklist -- still
holds.

**The question** — msm8998.dtsi describes venus fully and `BLOBS.md` lists the
firmware, so enabling hardware video decode looks like a config flip plus a
blob. Is it?

**The answer** — no. With `CONFIG_VIDEO_QCOM_VENUS=m`, the node `status =
"okay"` and `qcom/venus-4.4/venus.mbn` in place, `modprobe venus_core` **kills
the SoC instantly**. The modprobe never returns, and netconsole -- verified end
to end on both transports seconds earlier -- carries the marker printed one
line before it and then **nothing at all**. No oops, no call trace, no SMMU
fault. The phone comes back on the hardware watchdog.

That silence is the diagnosis. The CPU never got to report anything, which is a
**NoC timeout**, not a fault the kernel could catch. Anything that prints is
therefore the wrong instrument for the last step of this bring-up.

**Left autoloading, this is a bootloop**, and the bootloader does not fall back
on its own: five reboots from the initramfs did not exhaust the A/B retry
counter. The way out is a physical Power+VolDown into fastboot. Blacklisting
(`modprobe.blacklist=venus_core,venus_dec,venus_enc` on the cmdline) makes the
experiment cheap: the module can then be loaded by hand, and a hang costs one
watchdog reset back into a working system.

**What this rules out**

- *"It is the missing interconnect vote."* It genuinely is missing --
  `venus_runtime_resume()` calls `icc_set_bw()` on both paths *before*
  `core_power(POWER_ON)`, deliberately, and `devm_of_icc_get()` returns NULL
  rather than an error when the property is absent, so both votes were silent
  no-ops. Patch 0189 wires them, and the live devicetree was confirmed to carry
  `interconnects`/`interconnect-names` before the retest. **The outcome is
  byte-identical.** Real defect, wrong culprit.
- *"netconsole will show the boot-time probe."* It cannot. netconsole is armed
  from userspace, long after module autoload. An empty log across a boot-time
  probe proves nothing -- the first run of this experiment produced exactly
  that null and it was worthless. Blacklist at boot and load by hand instead,
  so the instrument is live during the window.

**Still open** — what actually dies. It is inside `core_power(POWER_ON)` or the
first register touch after it, below the level where printk can help. The next
instrument has to be one that survives an unresponsive fabric, not one that
prints.

**State left behind** — the series carries 0189 (bus vote, node still
`disabled`) and `CONFIG_VIDEO_QCOM_VENUS=m`, which is inert while the node is
disabled. `firmware-google-taimen` r4 ships `qcom/venus-4.4/venus.mbn`, which is
correct and harmless on its own. Enabling the node is one line and a bootloop;
do not do it casually.
