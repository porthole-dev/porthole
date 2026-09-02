---
id: epiphanys-frame-is-20ms-of-compositor-cpu-plus-a-10ms-gpu-tail-not-a5xx-batches
title: Epiphany's frame on m.youtube.com is ~20 ms of TextureMapper CPU walking 315 layers plus a ~10 ms GPU/FrameDone tail, serialized -- the kernel sees two submits per frame, so a5xx batch overhead is not the limit
scope: soc:msm8998
subsystem: graphics
severity: finding
confidence: proven
evidence: 2026-09-02 taimen, webkit r51/r52, uprobe timeline on renderLayerTree/didRenderFrame/frameDone + drm_msm_gpu:msm_gpu_submit(_retired), page state verified through the inspector
refutes: the compositor is a5xx batch-bound (100-170 kernel submits per frame); the window rate is the GPU clock; damage-for-compositing helps this page; YouTube's ambient canvas is the cost; frame time tracks CPU clock
first-learned: 2026-09-02
---

**The question** -- why does Epiphany update its window at ~30 fps on
m.youtube.com (1440p60 playing, video presented to WebKit at 60), and where
does each ~33 ms frame go?

**The answer** -- per frame (uprobe timeline, p50, idle playback, webkit
2.52.6 r51 and r52, `WEBKIT_LAYERS_TILE_SIZE=1440x1024`):

| phase | p50 |
|---|---|
| compositor CPU, `ThreadedCompositor::renderLayerTree` | 20-22 ms |
| GPU: first submit -> last retire (overlaps the paint) | 22-24 ms |
| last retire after paint end | 7-8 ms |
| paint end -> `AcceleratedSurface::frameDone` | 9-10 ms |
| `frameDone` -> next paint start | 0.1 ms |
| **period** | **31-34 ms** |

Nothing is pipelined: paint, then wait for the GPU tail and the UI process,
then paint again. 32 ms sits between 2 and 3 vsyncs, so frames alternate
33/50 ms -- that is the judder.

The CPU half is WebKit's own tree walks over 315 composited layers (188 of
them promoted only for `overlap`, 78 `willChange`): `prepareForPainting` 22%
(`computeTransformsRecursive` 19%, half of it the "50 ms future" transform
computed for every layer), `applyAnimationsRecursively` 9%, `collectDamage`
6%, `flushCompositingState` 3%, then ~75 `TextureMapper::drawTexture` calls
at ~125 us each (mesa `fd_draw_vbo` 15%, `fd_batch_flush` 12%,
`createEGLFence` 6.5%). `TransformationMatrix::multiply` alone is 11.6%.

The GPU half is fill: 73 quads per frame totalling ~61 Mpx device pixels
for a 3.7 Mpx viewport (~16x). 33 of them are full-width 1440x1024 tiles
belonging to only three page-sized layers (root, `negativeZIndexChildren`,
one 480x5102 `overlap` layer): `CoordinatedBackingStore::paintToTextureMapper`
draws every tile it holds, offscreen cover-rect tiles included. The
intermediate surface per frame is a trivial 120x121. GPU devfreq already
sits at 710 MHz.

The kernel sees **2 `msm_gpu_submit` per frame, 61-94 BOs each, 2 cmds
each**. libdrm's deferred-submit merging works on a5xx; `use_fence_fd` is
false for the intermediate batches.

**What this rules out** --
- "a5xx is batch-bound, 100-170 submits per frame": no, two. The earlier
  GALLIUM_HUD `batches` count was gallium batches, not kernel submits.
- "pin the GPU clock": it is already at 710 MHz during playback (`trans_stat`).
- "the CPU clock": the phone runs thermally capped (75 C passive trip, big
  cores 1.0-1.5 GHz) during every browser arm, yet fps was 27-30 at both
  1.13 and 1.88 GHz. The serialization dominates.
- "`UseDamagingInformationForCompositing`": neutral (25.6 vs 26.4 ms period)
  because WebKit's collected frame damage is the whole viewport every frame
  on this page. Not structural: a local page with one moving box damages
  only the box (`WEBKIT_SHOW_DAMAGE=1`, `--features=-UnifyDamagedRegions`).
  Which YouTube layer is whole-damaged each frame is still open; layer
  `paintCount`s do not change, so it is a transform/opacity/contents-layer
  change, not a repaint.
- "YouTube's ambient-mode canvas": removing both canvases changed nothing.
- "skip tiles whose paint is empty" (webkit r52, `skip-empty-tiles.patch`):
  draws 73 -> 58, period unchanged. The full-width tiles all paint something.

**How it was established** -- uprobes on `renderLayerTree` (entry+return),
`AcceleratedSurface::didRenderFrame`/`frameDone`, `TextureMapper::drawTexture`
with the FloatRect argument fetched (`w=+8(%x2):u32`), plus
`drm_msm_gpu:msm_gpu_submit`/`_retired` recorded system-wide; offsets are
`nm` vaddr minus the text LOAD delta (0x10000), and change with every
relink. Every arm asserted the page state through the inspector first
(`tk-webarm.sh`: `<video>` present, `play()`, `currentTime` advancing). Overturned
by: a page where the two halves are not serialized, or a kernel submit count
that scales with layers.

**What follows** -- 60 fps needs both halves under 16 ms: cull tiles outside
the clip in `paintToTextureMapper` (33 -> ~12 full-width draws), find and
fix the per-frame whole-viewport damage so damage-clipped compositing makes
an idle video frame cost only the player, and drop the "future" transform
walk when no layer has an `AnimatedBackingStoreClient`. Pipelining alone
(three buffers, paint N+1 while N renders) would give max(20, 24) ms --
still not 16.
