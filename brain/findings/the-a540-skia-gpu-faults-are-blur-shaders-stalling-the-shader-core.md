---
id: the-a540-skia-gpu-faults-are-blur-shaders-stalling-the-shader-core
title: The a540 GPU faults under Skia-GPU are Skia blur/downsample passes stalling SP/TPL1 -- not binning, not fp16, and a different class from the compositor's one VSC fault
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: taimen 2026-09-02, kernel 7.2.2 #28, mesa 26.1.6, webkit2gtk-6.0 2.52.6. Two devcoredumps of Skia-GPU faults decoded with mesa crashdec/cffdump (logs/gpucrash-2026-09-02-skia*.txt/.dec in the taimen repo). Fault 1: RBBM_STATUS 0xEE0011C1 = HLSQ|TPL1|SP|RB|UCHE busy, VSC/VPC idle; hung submit = sysmem pass into a 128x16 RGBA8 target sampling a 128x32 RGBA8 texture, one draw, FS with 48 samb (LOD-bias) taps into (f16) half regs, sampler MAX_LOD=0.125 MIPLVLS=0, PIXLODENABLE. Fault 2 (IR3_SHADER_DEBUG=nofp16): same status, GMEM batch of 112 draws over 512x64/128/32 intermediates, 144 samb, same sampler. tk-webbench Skia-GPU arm: 2 faults/2 runs; FD_MESA_DEBUG=nobin 1/3; IR3_SHADER_DEBUG=nofp16 2/3; page without backdrop-filter (still box-shadows) 3/5. The compositor-thread fault of the same day (GALLIUM_HUD arm) read 0xD30201C3 = VSC|VPC|UCHE busy, raster idle.
refutes: the a540 faults come from the hardware hang detector being misconfigured (a530-only mask quirk; series 0205 already carries the vendor threshold); they are VSC/binning overflow (nobin does not help); they are the fp16 sample-return path (nofp16 does not help); they happen "at any workload" (the page without blur still faults, but every hung submit decoded so far is a blur pass); Skia-GPU can be re-enabled by a flag
first-learned: 2026-09-02
---

**How to read one** -- `/sys/class/devcoredump/devcd*/data` (root) right after
the fault; it expires. `kernel: msm` text with the ring and the DUMP-flagged
BOs (freedreno marks every cmdstream BO, `fd_bo_new_ring`). mesa 26.1.6's
`crashdec` mis-parses this kernel's `revision: 540 (05040001)` line (patched
locally in `ref/mesa-26.1.6`) and walks the ring tail, not the hung submit;
the reliable route is `CP_IB1_BASE` (register byte offset 0x2c7c) -> that BO
-> repack as `.rd` (ascii85 words are big-endian; `RD_*ADDR` records are
`{lo, len, hi}`) -> `cffdump -v`. The decode names the render target, the
sampler/texture descriptors and disassembles the shaders.

**What both hung submits are** -- Skia blur passes: dozens of `samb`
(sample with bias) per pixel walking a downsampled chain, one sampler with
`MAX_LOD = 0.125` on a texture with `MIPLVLS = 0`, `PIXLODENABLE` set in
`SP_FS_CTRL_REG0`. The status says the shader core is waiting on the texture
pipe and never retires; the CP is parked. No SMMU stall (the driver checks
before printing), no CP_HW_FAULT.

**What was excluded, with counts** -- see evidence line. The one thing that
correlates with every decoded hang is the blur shader; the page-without-blur
arm still faults because `box-shadow` is a blur too.

**Where it goes** -- upstream freedreno with the two dumps. Until then
Skia-GPU stays off (`WEBKIT_SKIA_ENABLE_CPU_RENDERING=1`), which is also
why [[a-launch-that-skips-the-user-manager-loses-environment-d]] matters.
The compositor-thread fault (VSC/VPC busy) is a second, unexplained class
seen once, under GALLIUM_HUD overlay drawing.
