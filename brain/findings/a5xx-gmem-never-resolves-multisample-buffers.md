---
id: a5xx-gmem-never-resolves-multisample-buffers
title: GPU rasterisation is visibly wrong on a540 because the a5xx GMEM store never resolves multisample buffers
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: "taimen 2026-09-02, mesa 26.1.6, kernel #30. Local page (no network, no video): 80 cards x 5 filled SVG paths = 400 composited layers, WEBKIT_LAYERS_TILE_SIZE=1440x1024, scrolled. Rendered by the CPU rasteriser it is the reference; compared pixel for pixel: sysmem 0 differing px, default GMEM 3604, repeat 4218, nobin 3693, FD_MESA_DEBUG=flush 7645 and 10070, tile 1024x1024 3951, tile 512x512 (single bin, no tiling) 2954. Corruption is a solid rectangular band of unrelated coverage across a shape."
refutes: "the Skia-GPU corruption on a540 is UBWC; it is GMEM binning; it is the trailing partial bin or tile alignment; it is blur/backdrop-filter; it is texture tiling (notile); it is a shader or ir3 bug; it is a missing CCU flush at end of batch; enabling RB_CLEAR_CNTL.MSAA_RESOLVE fixes it"
first-learned: 2026-09-02
---

**The question** — with `WEBKIT_SKIA_ENABLE_CPU_RENDERING=1` removed, so Skia
rasterises on the GPU, WebKit renders icons and glyphs with blocks of garbage
in them. GPU rasterisation is worth having (it cuts the worst frame stall from
~2000 ms to ~720 ms, see
[[the-browser-stutter-is-a-blocked-webkit-main-thread]]), so what is wrong?

**The answer** — `fd5_emit_tile_fini()` does not flush the colour cache, and
the GMEM resolve writes through it.

```c
fd5_emit_sysmem_fini():                 fd5_emit_tile_fini():   /* GMEM */
  fd5_emit_lrz_flush()                    fd5_emit_lrz_flush()
  PC_CCU_FLUSH_COLOR_TS                   fd5_cache_flush()   /* UCHE only */
  PC_CCU_FLUSH_DEPTH_TS                   fd5_set_render_mode(BYPASS)
```

`fd5_cache_flush()` writes `UCHE_CACHE_INVALIDATE` -- the texture/unified
cache. It says nothing about the CCU, and `fd5_emit_blit()` resolves GMEM with
a `CCU_RESOLVE` event. A GMEM batch can therefore end with resolved colour
still in the colour cache, and a later batch that samples that texture reads
what was there before. WebKit's compositor does exactly that, hundreds of times
per frame: render a layer into a small FBO, then sample it.

**The measurement.** A local page (no network, no video, no YouTube): 80 cards
of 5 filled SVG paths = 400 composited layers, scrolled. The CPU rasteriser
renders it correctly, so it is the reference; every arm is diffed against it.

| arm | differing pixels |
|---|---|
| `FD_MESA_DEBUG=sysmem` | **0** |
| default (GMEM) | 3604 |
| default, repeat | 4218 |
| `nobin` | 3693 |
| `flush` | 7645, 10070 |
| tile 1024x1024 | 3951 |
| tile 512x512 (**single bin**) | 2954 |

sysmem is the only correct mode, and the only one that flushes the CCU.

**What this rules out**, each measured:

- **Binning** -- `nobin` is unchanged (3693 vs 3604).
- **Bin alignment / the trailing partial bin** -- a 512x512 layer tile is one
  GMEM bin with no tiling at all and still differs by 2954 px.
- **Tile size** -- 1440, 1024 and 512 all corrupt.
- **UBWC** -- impossible on a5xx anyway: `ubwc_ok = is_a6xx(screen)` in
  freedreno_resource.c and a5xx registers no modifier `is_format_supported`
  hook, so a540 advertises linear only. a5xx cannot render *to* UBWC either --
  `fd5_gmem.c` emit_mrt() hardcodes `RB_MRT_FLAG_BUFFER` to zero.
- **Texture tiling** -- `notile` does not help.
- **Blur / backdrop-filter** -- removing it does not help (4275 vs 3611); the
  amplifier is composited layers (985 without `will-change`/`contain`, ~4x less).
- **Shader/ir3** -- the hung-shader theory from the devcoredumps was a count of
  `samb` across a whole dump, not one shader; the disassembly around the fault
  is 67 instructions with one properly `(sy)`-synced sample.

`FD_MESA_DEBUG=flush` making it *worse* is consistent: it produces more
batches, and the defect is once per GMEM batch.

**The mechanism, narrowed to the shape.** The defect follows the *shape*, not
the position on screen. Same page, four icon slots:

| page | differing px | where |
|---|---|---|
| thumb, flag, bookmark, **triangle** | 4614 | only the triangle's column |
| **all four triangles** | 9078 | all four columns |

The triangle is the only one of the four with long diagonal edges, i.e. the
only one with many partially covered pixels. `emit_gmem2mem_surf()` stores
GMEM with the resolve hardcoded off and the condition commented out beside it:

```c
//	bool msaa_resolve = pfb->samples > 1;
bool msaa_resolve = false;
```

GMEM holds samples interleaved, so storing a multisample buffer without
resolving smears rows -- which is exactly the horizontal band of coverage seen
across the triangle, and exactly why axis-aligned icons survive.

**The framebuffers really are multisampled.** Proven by the negative: if
`samples` were 1 the patched builds below would be no-ops, and they are not --
they change the image drastically.

**Two patch attempts, both measured, both WRONG** (device reverted to stock
after each):

| build | change | mixed | all-tri |
|---|---|---|---|
| r1 stock | -- | 4614 | 9078 |
| r2 | `PC_CCU_FLUSH_COLOR_TS`/`DEPTH_TS` in `fd5_emit_tile_fini()` | 4237 | -- |
| r3 | `msaa_resolve = batch->framebuffer.samples > 1` | 32384 | 68265 |
| r4 | r3 + RAS at source samples, DEST forced `MSAA_ONE` (the split a4xx makes) | 78914 | 68530 |

r2 changed nothing, so the CCU-flush theory is dead. r3 and r4 made it far
worse, so the resolve needs state this note does not know.

**What is still true**: `FD_MESA_DEBUG=sysmem` is pixel-exact (0 differing px)
and cuts the worst frame stall from ~2000 ms to ~720 ms.

**What the next person needs** -- not another guess. The correct a5xx GMEM
store sequence for a multisample buffer is only knowable from a command-stream
trace of the Qualcomm blob (how freedreno was reverse engineered in the first
place); the vendor *kernel* (`ref/downstream-wahoo`, kgsl) confirms only
`gmem_size = SZ_1M` for a540, which mainline already matches, and contains no
resolve logic because the resolve lives in the proprietary userspace driver.
a6xx is not a usable model either -- it resolves implicitly through its blit
event type, with no equivalent flag.

**Building mesa to test this is now a solved problem**: `pmaports/temp/mesa`
carries Alpine's aport (26.1.6) plus a patch, ~20 min per iteration. Two traps
are baked into that aport copy: pmbootstrap does not evaluate `case "$CARCH"`
blocks, so conditional makedepends must be hoisted to the top level
(porthole#54), and its crossdirect cross-compiles C/C++ but not Rust, so the
nouveau/rusticl Rust components must be dropped or they link as x86-64.

**Meanwhile**, `FD_MESA_DEBUG=sysmem` gives correct GPU rasterisation on this
device, measured: worst frame stall 721/716 ms against 1987/1832 ms for the
shipped CPU rasteriser, at 0 differing pixels. It costs the GMEM bandwidth
advantage, so it is a stopgap for the browser, not a system-wide default.

**How to re-check it** -- the reproducer is `icons4.html` plus a diff against a
CPU-rasterised capture of the same page. Any arm that is correct *without*
flushing the CCU would overturn this.
