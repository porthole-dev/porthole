---
id: youtube-video-freezes-are-a-software-css-blur-on-the-main-thread
title: The multi-second YouTube freezes are a CSS blur() painted in software on the web process main thread, because the GTK port never composites a layer for its filter
scope: device:google-taimen
subsystem: video
severity: finding
confidence: proven
evidence: 2026-09-06, webkit2gtk-6.0 2.52.6-r61/r62, epiphany 50.6, m.youtube.com Big Buck Bunny 60fps at 1440p and 2160p60 (WEBKIT_GST_VIDEO_DECODING_LIMIT). WAYLAND_DEBUG commit log of the UI process plus eu-stack of the whole web process whenever its compositor thread idled >300 ms (tools/repro/video-base/vidbase.sh + tools/ph-stallcatch.py, staged as /tmp/vidbase.sh and /tmp/stallcatch2.py). Ten commit gaps of 1.4-3.2 s per 100 s at 1440p, presented-every-N-vsyncs p50 2; before each gap the compositor answered the previous commit in <10 ms. In all 8 captures the main thread was R in SkBlurEngine <- SkBlurImageFilter::onFilterImage <- FEGaussianBlurSkiaApplier::apply <- FilterEffect::apply <- RenderLayer::updateFilterPaintingStrategy <- paintLayerByApplyingTransform <- paintLayerWithEffects <- paintList (symbolized with the matching .debug, vaddr = file offset + 0x10000). The element is a 720x675 div with filter blur(40px) and a 1.5x2.5 2D transform (YouTube ambient mode). Forcing the page's 47 filtered elements composited from the inspector (will-change: transform) removed the holes: max gap 3.2 s -> 0.29 s, but compositor p50 34 -> 45 ms.
refutes: the multi-second video freezes are the compositor, the GPU, venus, the media pipeline, GC, thermal throttling or a futex deadlock on the main thread
first-learned: 2026-09-06
---

**The question** -- YouTube in Epiphany freezes for one to three seconds every
ten or so, then catches up; the decoder is fine, the compositor is fine, the
main thread was reported "blocked in a futex". What is stopping the picture?

**The answer** -- a CSS `filter: blur(40px)` layer -- YouTube's ambient mode,
a blurred copy of the video behind the player -- is rasterised in software by
Skia on the web process MAIN thread, inside the render layer paint, every time
that layer repaints. On the a540 phone's CPU that is 1.4-3.2 s per repaint.
While it runs the rendering update never finishes, the UI process gets no new
frame, and the media element stops counting presented frames. Nothing is
blocked: the main thread is `R` the whole time, in `SkBlurEngine`.

Why software: `WebChromeClient::allowedCompositingTriggers()` has no
`FilterTrigger` for any WebKit2 port, so
`RenderLayerCompositor::requiresCompositingForFilters()` never gives a layer
its own compositing layer for the filter alone, and the software path
(`RenderLayerFilters` -> `FilterEffect` -> `FEGaussianBlurSkiaApplier`) runs.
On Cocoa that path is Core Image and cheap; here it is `SkBlurImageFilter` on
the CPU. Coordinated Graphics already knows how to composite filters
(`GraphicsLayerCoordinated::setFilters`, `TextureMapper::applyBlurFilter`
with downsampling for large radii, on the GPU, on the compositor thread) --
only the trigger is missing.

The fix is `temp/webkit2gtk-6.0/composite-css-filters-on-coordinated-graphics.patch`
(r63): add `FilterTrigger` under `USE(COORDINATED_GRAPHICS)` and, on that
path, composite only filters that move pixels (`Style::Filter::
hasFilterThatMovesPixels()`, i.e. blur and drop-shadow; reference filters
stay in software because `GraphicsLayerCoordinated` cannot composite them).
The gate matters: compositing every filter puts 47 intermediate surfaces per
frame on this page (mostly `brightness(0.9)` thumbnails) and cost 34 -> 45 ms
of compositor period in the inspector experiment.

**What this rules out** -- the compositor, TextureMapper paint cost, the GPU
clock, venus, the V4L2 buffer pool, the MSE append path, JavaScriptCore GC,
thermal throttling and "a futex the main thread waits on" as the cause of the
multi-second holes. Each of those was measured on 2026-09-02..05; none could
have produced a main thread that is *running* in a blur for 2 s. It also
rules out the r57 frame-pipelining patch as a fix for the holes -- it
addresses the 32 ms period, not the seconds.

**How it was established** -- the UI process's `wl_surface.commit` gaps were
correlated with the Wayland log around each gap (the compositor had already
answered; the client was idle), then the web process was caught mid-hole
with `eu-stack` and symbolized. Two earlier catcher runs produced clean nulls
for two instrument reasons, both now in
`brain/traps/a-stall-catcher-must-pick-the-busiest-webkitwebprocess.md`.
Then the patch was measured: on r63, same arm, same page, 1440p delivers
175-186 frames every 3 s sample with 0 dropped and a longest UI commit gap of
179 ms (r61: ten gaps of 1.4-3.2 s per 100 s); 4K60 delivers 187-226 per
sample, 0 dropped, longest gap 285 ms (r62: 2.9 s and whole samples frozen).
The compositor thread never idled 300 ms in either arm. Cost: commit p50
34-36 -> 38.5 ms at 1440p. What would overturn it: the holes back on r63+
with a different stack in the catcher.
