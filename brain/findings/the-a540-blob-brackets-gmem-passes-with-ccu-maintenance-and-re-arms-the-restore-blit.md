---
id: the-a540-blob-brackets-gmem-passes-with-ccu-maintenance-and-re-arms-the-restore-blit
title: The a540 blob brackets every GMEM pass with CCU invalidate/flush and re-arms the restore blit rectangle; fd5 does neither
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: probable
evidence: 2026-09-04, static reconstruction of libGLESv2_adreno.so (Pixel 2 XL vendor.img) tile loop at nm 0x1100e8: every CP_EVENT_WRITE/WFI/SET_RENDER_MODE is a rodata packet template (.rodata 0xa850-0xbd60) copied after the ring-alloc call at 0xb9bf8, so all event ids resolve statically; restore/resolve blits are pre-recorded IBs selected per tile by 0x30fb00. Proven from the binary: PC_CCU_INVALIDATE_COLOR+DEPTH at GMEM tile-mode begin (0x2ed668); PC_CCU_FLUSH_COLOR_TS + PC_CCU_FLUSH_DEPTH_TS + CACHE_FLUSH_TS before the UCHE invalidate at pass end (0x2ee050); per restore blit RB_CNTL=0x00020000 (BYPASS, no width/height) and RB_RESOLVE_CNTL_1/2 rewritten immediately before CCU_RESOLVE (0x232f88); no WAIT_FOR_ME before SET_BIN_DATA5 (0x30f6b0). fd5_gmem.c 26.1.6: no CCU invalidate in the GMEM path, tile_fini = LRZ flush + UCHE invalidate + WFI only, mem2gmem keeps RB_CNTL=W|H|BYPASS and the rectangle from tile_prep. Files: ~/adreno-blob/tileorder/{tile_sequence,diff_vs_mesa,helpers}.txt.
refutes: the blob's tile loop is packet-for-packet what fd5 emits; the vendor difference must be a register mesa never writes; the mem2gmem restore in fd5 is set up the way the vendor sets it up
first-learned: 2026-09-04
---

**The question** -- [[the-a540-blob-writes-no-a5xx-register-that-mesa-and-the-kernel-do-not]]
left packet order and cache-maintenance events as the blind spot. Around
a GMEM tile's restore blit, where the 09-03 lockup parked (RB and UCHE
busy, ME on the WFI that opens the draw IB), does the vendor driver emit
something fd5 does not?

**The answer** -- three things, all proven from the binary because the
blob keeps its packets as rodata templates rather than building headers
at run time:

1. At the start of every GMEM pass, between the LRZ flush and the
   PC/VFD power and RB_CCU_CNTL setup: `PC_CCU_INVALIDATE_COLOR` and
   `PC_CCU_INVALIDATE_DEPTH`. fd5 emits neither in the GMEM path (only
   `PC_CCU_INVALIDATE_COLOR` in sysmem_prep), so CCU lines the previous
   submit left behind survive into the first restore.
2. At the end of the pass, before the UCHE invalidate + WFI:
   `PC_CCU_FLUSH_COLOR_TS`, `PC_CCU_FLUSH_DEPTH_TS`, `CACHE_FLUSH_TS`.
   fd5's tile_fini only invalidates.
3. Every restore blit runs with `RB_CNTL = BYPASS` and width/height 0,
   with `RB_RESOLVE_CNTL_1/2` rewritten right before the `CCU_RESOLVE`
   event. fd5 keeps the bin size in the bypass RB_CNTL and programs the
   rectangle once per tile before `SET_BIN_DATA5`.

The blob also skips the `WAIT_FOR_ME` fd5 emits before `SET_BIN_DATA5`
(mesa is stricter there) and never waits between the restore and the
draws; fd5's wait there is the first draw's `fd_wfi`, which is where the
ME was parked -- the witness of a stuck restore, not its cause.

**What this rules out** -- "the tile loops are the same, so a mesa-side
fix is impossible". Not yet established: that items 1 and 2 end the
lockups; mesa r14 (`temp/mesa/a5xx-tile-parity.patch`) applies those two
and the persistent dump saver is the hang instrument.

**Item 3 transplanted literally into fd5 is WRONG** (mesa r13, 2026-09-04
20:29): with `RB_CNTL = BYPASS` and no width/height, and the rectangle
re-armed before each `CCU_RESOLVE`, every one of the 18 GMEM tiles came
back holding a shrunken copy of the whole framebuffer -- the RB blits the
entire surface into each tile. The blob's restore IB runs in a context
(its own RB_MRT/scissor/window state in the pre-recorded IB, decoded
only by role) that fd5's mem2gmem does not reproduce, so the bypass
RB_CNTL cannot be copied on its own. Do not retry it without decoding IB
slots 5/7/8 first.

**How it was established** -- Ghidra 12.1.2 headless for function bounds
and 65 base-corrected decompiles (the project loads at +0x100000, which
had invalidated two earlier passes), then llvm-objdump text: PKT7 header
scan with parity filter, the rodata template table with its code
references, the tile-loop vtable at 0x333f20 resolved to slots, and
address-ordered emission skeletons per phase. IB slots 5/7/8 (the
resolve variants) are inferred by role, not decoded; `decomp_all3.c` has
them ready. Scripts: `~/adreno-blob/tileorder/{scan7,rodata_refs,skel}.py`.
