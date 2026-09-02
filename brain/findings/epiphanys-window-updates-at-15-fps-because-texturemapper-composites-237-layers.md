---
id: epiphanys-window-updates-at-15-fps-because-texturemapper-composites-237-layers
title: Epiphany's window updates at 12-30 fps on YouTube because WebKit's compositor thread spends 35-50 ms per frame on a 237-layer page -- not the video, not GTK, not the GPU clock, not damage tracking
scope: device:google-taimen
subsystem: graphics
severity: finding
confidence: proven
evidence: taimen 2026-09-02, webkit2gtk-6.0 2.52.6, kernel #28, CPU rendering. Compositor thread ("eadedCompositor") 58-70% of a core at 20 frames/s (35 ms/frame); GTK frame clock paints in 2.6 ms then sleeps 66-75 ms (GDK_DEBUG=frames). LayerTree.layersForNode via the inspector: 237 composited layers (146 overlap, 60 willChange, 12 transform3D), root 480x5112 CSS px at DPR 3. perf on the thread: 36% libgallium, 18% kernel (msm_gem_pin/get_pages/clear_page/dma_resv), 8% memcpy, 29% WebKit spread thin. GALLIUM_HUD dump: ~40 draws and 100-170 "batches" per period. Arms (GTK interval p50 during 1440p/1080p playback): GPU pinned 710 MHz 56 ms vs 50 unpinned; WEBKIT_GST_DMABUF_SINK_DISABLED=1 42 vs 50; MiniBrowser --features=-PropagateDamagingInformation 27.4 vs 26.9; 1080p vs 1440p 38 vs 50; MiniBrowser at 640x360@30 still 35; WEBKIT_LAYERS_TILE_SIZE 512/1440x512/1440x1024/2048/4096 = 40-51/28/26/23-27/24 ms; drag commits p50 512: 32.6, 1440x1024: 23.0, 2048: 27.3. A 63-layer local page with will-change cards composites at 16.7 ms.
refutes: the residual judder is venus or the V4L2 queues; it is WebKit dropping video frames; it is GTK's GL renderer; it is the GPU clock; it is damage propagation; it is the DMABuf-vs-GLMemory video import; it scales with video resolution; phoc drops the frames
first-learned: 2026-09-02
---

**The question** -- with hardware decode clean (60 fps, venus at 444 MHz),
`getVideoPlaybackQuality()` reporting 60 presented frames/s and ~0 drops,
and the panel at 60 Hz, the user still sees frames repeat and scrolling
"throughput drop". Where do the frames go?

**The answer** -- they never leave the web process fast enough. The
threaded compositor takes 35-50 ms of CPU per composited frame on
m.youtube.com, so the UI process gets a new buffer every 3-5 vsyncs; GTK
paints it in under 3 ms and idles; phoc shows each buffer several times.
Scrolling is the same machine at ~30 fps. The cost is the page, not the
video: it barely moves between 1440p and 640x360, a 63-layer synthetic page
composites at 60 Hz, and the YouTube page composites at ~37 Hz with the
video *paused*.

**What the profile says** -- a third of the thread is mesa's CPU side, a
fifth is the kernel pinning/allocating GEM objects per submit, and WebKit
itself is spread over dozens of small functions (lld's ICF makes exact
names unreliable). That is the shape of "too many small GL objects per
frame": hundreds of 512x512 tile textures across 237 layers, each a BO to
pin per submit and a draw with state changes, plus 100+ render passes
(batches) per period from intermediate surfaces. It is TextureMapper on a
tiler with a CPU-heavy driver, and upstream-shaped.

**What moves it** -- tile size, and nothing else tried:

| arm | GTK interval p50 | note |
|---|---|---|
| 512 (default) | 40-51 ms | drag p50 32.6 ms |
| 1440x512 | 28 ms | |
| **1440x1024** | 26 ms | drag p50 23.0 ms, +44 MB GEM -- shipped in environment.d |
| 2048 | 23-27 ms | drag p50 27.3, +85 MB |
| 4096 | 24 ms | 64 MB per tile |

GPU clock pinned to 710: no change (the thread is CPU-bound). Damage
propagation off: no change. gst-gl import instead of WebKit's per-plane
EGLImage import: 42 vs 50 ms, second order. Resolution: second order.

**Ceiling after the tile change** -- ~25 ms per frame = ~40 fps window
updates during video, ~43 fps drags. Buttery needs 16 ms, i.e. roughly
half the per-frame compositor cost again. The remaining levers are all
upstream WebKit/mesa: fewer intermediate surfaces and draws per layer in
TextureMapper, batching, and freedreno's per-draw/per-batch CPU cost on
a5xx. Layer count itself is the page's (146 layers exist only because they
overlap `will-change` cards).

**Instruments left behind** -- `tools/tk-webvq.py` (presenter counters via
the remote inspector), `tools/tk-weblayers.py` (composited layer census
with WebKit's own reasons), `tools/tk-rangehttp.py` (range-capable local
server for `<video>` benches; WebKit's `<video>` did not load from it
during this session, unresolved), and the method in
[[the-dpu-counter-is-phocs-frame-rate-not-the-apps]].
