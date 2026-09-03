---
id: the-a5xx-first-tile-restore-runs-with-the-previous-submits-msaa-state
title: The phosh top-right strip is the FIRST GMEM tile, restored with the previous process's MSAA registers -- fd5 tile init never programs them
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: "taimen 2026-09-03, mesa 26.1.6 r8-r12, kernel 7.2.2 #31, phoc 0.57. 40-capture rate per arm over a panel region no client repaints (corrupt = grey stddev > 0.05; every clean arm also checked pixel-black, mean 0). default 40/40; PHOC_DEBUG=damage-whole 0/40; FD_MESA_DEBUG=nosbin right 0/40 and LEFT 40/40; software browser 0/40; GPU browser on a single-layer page 0/40; glmark2 fullscreen 1/35 (its last tile is bright, so a true negative). Diagnostic mesa r11 with an FD5_HACK bitmask at tile init: all bits 0/40, none 40/40; {fast clear, double restore} 40/40; {RB_CNTL, WFI, CCU_CNTL toggle} 40/40; {CCU flush, UCHE inval, CCU inval, MSAA} 0/40; then singly: CCU flush 40/40, UCHE inval 37/40, CCU inval 40/40, emit_msaa() 0/40. r12 = emit_msaa() in fd5_emit_tile_init: **0/40, mean 0** (right strip and left region, 2026-09-03 16:20). Command streams of phoc and WebKitWebProcess captured with FD_RD_DUMP on the triggering and the harmless page and diffed for the final value of every register."
refutes: "the strip is the clipped 416-wide third bin column (416 % 64); it is the resolve rect or CCU placement (r6/r7); it is a missing CCU invalidate at tile init (r9, and again as hack bit 0x080); it is inherited blend state / RB_MRT_CONTROL.BLEND2 (r10, 40/40); it is the restore blit being dropped (a second restore changes nothing); it is a5xx preemption (one ring); it is per-process pagetable, TLB or UCHE aliasing (a5xx has one shared VM here); it is hw binning or the scissor optimisation (nobin/noscis); it is intermittent"
first-learned: 2026-09-03
---

**The question** -- with a browser maximised, the phosh statusbar from x = 1024
rightwards fills with shredded browser pixels. Handoff of 2026-09-03 AM had it
down as "the clipped third GMEM bin column" after five refuted hypotheses.

**The answer** -- `fd5_emit_tile_init()` programs zs and mrt for the GMEM pass
but not the sample count. `emit_msaa()` is first called from
`fd5_emit_tile_renderprep()`, which runs *after* the first tile's mem2gmem. So
the binning pass and the first restore of every batch run with whatever
`RB_RAS_MSAA_CNTL` / `RB_DEST_MSAA_CNTL` (and the GRAS/TPL1 pair) held when the
previous submit ended. On a5xx nothing restores register state between
submits, so with a second process on the GPU that is *its* state. WebKit's
Skia mask passes leave 4x behind; the restore then copies with a four-sample
layout and the undamaged rows of the first tile come back sample-interleaved
-- the same shredding as the unresolved MSAA store of
[[a5xx-gmem-never-resolves-multisample-buffers]], which is what the pictures
always looked like. `fd5_emit_sysmem_prep()` has called `emit_msaa()` all
along; the tile init simply never did.

**Why it looked like the third column.** The tile order is an 'S'
(`freedreno_gmem.c`, "Swap the order of alternating rows"): the top row runs
right to left, so the first tile rendered is (1024, 0). `FD_MESA_DEBUG=nosbin`
disables the swap and the corruption moved to the top-left tile (left panel
region 40/40, right strip 0/40).

**Why only undamaged pixels, and only with some clients.** Damaged pixels are
redrawn after the restore; `PHOC_DEBUG=damage-whole` measured 0/40. A client
whose submits end single-sampled (glmark2, a single-layer page, software
rendering) leaves nothing to inherit: all 0/40.

**How the fix was found once the register diff ran dry.** A diagnostic mesa
(`vendor-patches/mesa/diagnostic/a5xx-hack-tile-init.patch`) took an
`FD5_HACK` bitmask from `~/.phoshdebug` and switched candidate operations on
at tile init, so one 20-minute build served nine 5-minute arms:

| bits | what | right strip |
|---|---|---|
| 0x3fb | everything | 0/40 |
| 0 | nothing (control on the same build) | 40/40 |
| 0x003 | fast clear of tile 0 GMEM; restore twice | 40/40 |
| 0x218 | RB_CNTL in GMEM mode; WFI after mode switch; CCU_CNTL toggle | 40/40 |
| 0x1e0 | CCU flush; UCHE inval; CCU inval; MSAA regs | 0/40 |
| 0x020 / 0x040 / 0x080 | CCU flush / UCHE inval / CCU inval, singly | 40/40, 37/40, 40/40 |
| **0x100** | **`emit_msaa(ring, pfb->samples)`** | **0/40** |

The second restore not helping is what separated "the blit is dropped" from
"the blit copies wrongly".

**Measurement traps found today**, on top of the AM handoff's:

- The stddev oracle is blind to a flat *non-black* panel; every clean arm was
  re-checked for `mean == 0`. glmark2's last tile is bright, so its 1/35 is a
  real negative.
- On the charger the battery icon enters the sampled strip: score
  `CROP=230x75+0+0` (x 1024..1253), or a clean panel reads 100%.
- `echo N > $FD_RD_DUMP_PATH/phoc_trigger` does nothing: phoc opens the GPU
  twice, one close unlinks the trigger, the submitting device reads a deleted
  inode. `tools/repro/a5xx-gmem/rd-trigger.sh` writes through `/proc/<pid>/fd`.
- greetd's `initial_session` only fires when greetd starts; after
  `terminate-session` you get the greeter, and the relogin needs the account
  password (`TK_LOGIN_PASSWORD`, kept in a 0600 file).
- A register-state diff of the previous submit is the right instrument, but
  read the *whole* survivor list: MSAA was in it from the start and was
  discounted because 7 of 8 captured WebKit submits ended single-sampled.

**Fix**: `pmaports/temp/mesa/a5xx-tile-init-msaa.patch`, one call. Upstreamable
as is.
