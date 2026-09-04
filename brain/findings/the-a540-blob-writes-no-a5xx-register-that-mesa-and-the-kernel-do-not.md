---
id: the-a540-blob-writes-no-a5xx-register-that-mesa-and-the-kernel-do-not
title: The a540 userspace blob writes no a5xx register that mesa fd5 and the kernel do not already write
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: probable
evidence: 2026-09-04 static scan of libGLESv2_adreno.so from the Pixel 2 XL vendor.img (Ghidra 12.1.2 in the arch distrobox for function bounds, llvm-objdump text + python for the PKT4 scan with the parity bit as a filter): 690 code sites, 568 distinct a5xx offsets, against 772 written by mesa fd5 and 231 by a5xx_hw_init. The remainder is GRAS_CL_GUARDBAND_CLIP_ADJ, tessellation/GS/XFB/compute state, TPL1 pointer-bound sampler tables, UBWC 2D flags, debug-bus and perf counters. Instrument and table: profiles/google-taimen/tools/adreno-regdiff/ (scan_blob.py, a5xx_regs.py, ref_regs.py, diff_regs.py, candidates_ranked.txt); the 27 MB disassembly and Ghidra project stay in ~/adreno-blob/.
refutes: a vendor-only workaround register explains the a540 hangs; the blob programs DBG_ECO, MODE_CNTL, TIMEOUT or cache registers mesa never touches; the missing piece for the RB/UCHE/VSC stalls is a PKT4 mesa lacks
first-learned: 2026-09-04
---

**The question** -- does the vendor's userspace GL driver program an a5xx
register that mesa's fd5 driver and the mainline a5xx init never touch, so
that a missing workaround could explain the rare RB/UCHE/VSC lockups?

**The answer** -- no. Every RB, UCHE, VSC, GRAS_SC, RB_RESOLVE, HLSQ_CONTROL,
VFD, PC and SP register the blob emits as a CP_TYPE4 packet is also written
by mesa fd5 or by `a5xx_hw_init()`. The blob never programs RBBM, UCHE, CP
or VBIF configuration from the command stream at all (only the
UCHE_CACHE_INVALIDATE range, which mesa also emits). The residue is
GRAS_CL_GUARDBAND_CLIP_ADJ (a clip-robustness value mesa writes on fd6 but
not fd5), tessellation/GS/transform-feedback/compute state mesa's a5xx has
no feature for, TPL1 sampler tables bound by pointer instead of
CP_LOAD_STATE4, UBWC 2D flags, and debug-bus/perf-counter registers.

**What this rules out** -- "the blob sets a DBG_ECO/MODE_CNTL/TIMEOUT/cache
register mesa never does". Not ruled out, because the scan cannot see it:
a different *value* in a register both write (RB_CNTL, GRAS_SC_CNTL, the
UCHE invalidate fields), a different packet *order* (CP_EVENT_WRITE, WFI,
CP_WAIT_FOR_ME around tile restores), or state emitted through
CP_LOAD_STATE4 / CP_SET_DRAW_STATE indirect buffers built at run time.
A live command-stream capture on Android would answer those; static
analysis will not.

**How it was established** -- Ghidra 12.1.2 headless (arch distrobox,
project `~/adreno-blob/proj/adreno.gpr`) for function bounds and 21
decompiled emitters, then `llvm-objdump` text and `profiles/google-taimen/tools/adreno-regdiff/scan_blob.py`: every
immediate that decodes as a CP_TYPE4 header with the register parity bit
matching (`pm4_odd_parity_bit`) and an offset present in mesa's
`a5xx.xml`. 690 sites, 568 distinct offsets, attributed to functions via
the `.gnu_debugdata` symbol table. Reference sets from `OUT_PKT4()` in
`src/gallium/drivers/freedreno/a5xx/` (772 offsets) and `gpu_write()` in
`a5xx_gpu.c` (231). Headers computed from a variable base are invisible
to the scan; the reverse diff (mesa writes, blob does not) is dominated by
exactly those indexed registers and was not trusted.
