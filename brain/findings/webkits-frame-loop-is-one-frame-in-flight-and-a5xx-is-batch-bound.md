---
id: webkits-frame-loop-is-one-frame-in-flight-and-a5xx-is-batch-bound
title: WebKitGTK's frame loop allows one frame in flight, released from GTK's snapshot(); on a5xx every render pass is a separate kernel submit -- so the compositor's cost is passes, not pixels
scope: soc:msm8998
subsystem: graphics
severity: finding
confidence: proven
evidence: Read in webkitgtk-2.52.6 and mesa 26.1.6 / linux 7.2 sources (file:line in the body). Measured 2026-09-02 on taimen: compositor thread 99.6% inside ThreadedCompositor::renderLayerTree, 51% under TextureMapper::drawTexture (mesa 36%, kernel 24%, libdrm 16% inclusive), 25% intermediate surfaces (applyBlurFilter 5%), 13% flushCompositingState (tile updateContents 8%), 8% prepareForPainting; GDK frame clock paints 2.6 ms then sleeps 66-75 ms; strace 78 GEM_SUBMIT, 377 GEM_NEW, 3246 GEM_MADVISE in 4 s; GALLIUM_HUD 100-170 batches per period for ~40 draws. Earlier symbolization of this profile was wrong by the text segment's vaddr-offset delta (0x10000) -- the "ICOImageDecoder on the compositor thread" names were that bug.
refutes: the browser's window rate is limited by GTK or the UI process; it is the GPU clock (pinned 710 MHz changed nothing); it is damage propagation; a deeper swapchain alone would help; draw calls are the cost (they are ~40/frame)
first-learned: 2026-09-02
---

**The loop** (`Source/WebKit/...`): `ThreadedCompositor::renderLayerTree`
sets `InProgress` and nothing rearms its render timer until
`AcceleratedSurface::FrameDone` arrives (`ThreadedCompositor.cpp:464-510`,
`AcceleratedSurface.cpp:1116`). The UI process sends `FrameDone` from
`AcceleratedBackingStore::snapshot()` (`AcceleratedBackingStore.cpp:800-811`),
i.e. from inside GTK's snapshot vfunc, after the render fence signalled
(`FenceMonitor`). Swapchain: `s_initialBuffers = 2`, `FIXME` for triple
buffering (`AcceleratedSurface.h:361`). So period = compositor paint + fence +
wait for the next GTK tick. `EGL_ANDROID_native_fence_sync` is present on
taimen, so there is no clientWait on the compositor thread.

**Per frame, five walks of the whole layer tree**: flushCompositingState
(one lock per layer; backing-store layers never early-out on a
RenderingUpdate), applyAnimationsRecursively (twice per layer),
computeTransformsRecursive, collectDamage (PropagateDamagingInformation is
GTK-default true; its result only feeds GTK's update region because
UseDamagingInformationForCompositing is false -- the viewport is repainted
whole), then paint: unbatched `glUseProgram/bind/uniforms/glDrawArrays` per
quad. `BitmapTexture::reset()` deletes FBO + depth + stencil renderbuffers on
every pool re-acquire (`BitmapTexture.cpp:135-153`); flattened 3D subtrees
are destroyed every frame (`TextureMapperLayer.cpp:373`).

**Why a5xx pays per pass** (mesa 26.1.6): gen<6 forces a fence per batch
(`freedreno_batch.c:113-118`) so submits never merge -- one render pass =
one `DRM_IOCTL_MSM_GEM_SUBMIT`; the suballoc heap is gen>=6 only
(`freedreno_device.c:115`) so every ring chunk and resource is its own GEM
handle; the kernel then does `drm_exec` lock + pin + `dma_resv_add_fence`
per BO per submit (`msm_gem_submit.c:299-437`), with no VM_BIND path
reachable on a540 (no per-process pgtables, `adreno_gpu.c:556-559`). Every
draw is also recorded twice (binning IB built unconditionally,
`fd5_draw.c:110-119`). Any batch with a clear or >5 draws goes GMEM
(`freedreno_autotune.c:137-149`).

**What follows** -- the levers are (a) fewer intermediate surfaces / FBO
switches per frame in TextureMapper (each is a GMEM pass: binning, per-tile
restore+draw+resolve), (b) keep FBO/renderbuffers across
`BitmapTexture::reset`, (c) send `FrameDone` when the fence signals rather
than from `snapshot()` with three buffers, so period becomes max(paint,
vsync). Tile size (`WEBKIT_LAYERS_TILE_SIZE`) halves the cost because it
halves tiles and BOs per submit. GPU clock and damage flags are not levers;
measure any of this with the client's own commits, never the DPU counter
([[the-dpu-counter-is-phocs-frame-rate-not-the-apps]]).
