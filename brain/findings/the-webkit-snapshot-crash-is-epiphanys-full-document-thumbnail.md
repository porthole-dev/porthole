---
id: the-webkit-snapshot-crash-is-epiphanys-full-document-thumbnail
title: The WebKitWebProcess SIGSEGV is Epiphany asking for a FULL_DOCUMENT snapshot of a 237522 px page: Skia refuses a raster surface over 2 GB and WebKit dereferences the null
scope: generic
subsystem: browser
severity: finding
confidence: proven
evidence: 2026-09-06, webkit2gtk-6.0 2.52.6-r61, epiphany 50.6-r0, two SIGSEGV coredumps (pids 17489/17510) one second apart. Symbolized on the host with llvm-symbolizer against the r61 -dbg .debug: getCachedCanvas (SkSurface_Base.h:208) <- SkSurface::getCanvas <- ShareableBitmap::createGraphicsContext (ShareableBitmapSkia.cpp:68) <- ImageBufferShareableBitmapBackend::create <- WebChromeClient::createImageBuffer <- WebImage::create <- WebPage::snapshotAtSize (WebPage.cpp:3282) <- WebPage::takeSnapshot. gdb on the core (arch distrobox, set sysroot + build-id debug dir) shows the ShareableBitmapConfiguration at 2802 x 237522 px = 2.66 GB. Skia's SkSurfaceValidateRasterInfo refuses height*rowBytes > SK_MaxS32 and SkSurfaces::WrapPixels returns nullptr; the caller dereferences it. Epiphany's lib/ephy-snapshot-service.c passes WEBKIT_SNAPSHOT_REGION_FULL_DOCUMENT one second after every top-history page load (embed/ephy-web-view.c, g_timeout_add_seconds 1) and then scales to 650x540. Upstream WebKit main (checked the same day) has the same missing null check.
refutes: the WebKitWebProcess SIGSEGV on r61 is a scroll or video patch, a GPU fault, memory pressure or an r61 regression
first-learned: 2026-09-06
---

**The question** -- Epiphany's page died twice in a row on r61 with a
SIGSEGV deep in `libwebkitgtk`, right after a long Wikipedia article loaded.
Was it the new scroll patches, the video patch, or the GPU?

**The answer** -- none of them. One second after a top-site finishes loading,
Epiphany asks WebKit for a `FULL_DOCUMENT` snapshot for the overview
thumbnail. WebKit sizes that bitmap from the frame's `contentsSize` times the
device scale factor: 2802 x 237522 px for that article on this phone, 2.66 GB.
`SharedMemory::allocate()` hands out the memfd, Skia's `WrapPixels` refuses
anything over 2 GB, and `ShareableBitmap::createGraphicsContext()` calls
`getCanvas()` on the null surface. Upstream main has the same bug.

Two fixes, both upstream-bound: `temp/webkit2gtk-6.0/shareable-bitmap-null-surface.patch`
(r62: null-check, drop the ref Skia did not release, the snapshot completes
empty) and a new `temp/epiphany` fork (50.6-r50,
`overview-thumbnail-from-the-visible-region.patch`: ask for the VISIBLE
region -- the thumbnail is the top of the page at viewport width either way).
The Epiphany side is the one that matters for smoothness: pages under the
limit still paint the whole document synchronously on the main thread into
hundreds of MB of shared memory on every top-site load.

**What this rules out** -- the r61 scroll and video patches (the crash path
never touches them), GPU faults (none logged), memory pressure as the trigger
(the allocation succeeds; Skia's size check fails), and "r61 is bad" as a
whole -- the same request crashes r50.

**How it was established** -- `coredumpctl info` offsets against the
-dbg package's `.debug` on the host (minutes, no device time), then the
bitmap geometry read out of the core in gdb. What would overturn it: a
SIGSEGV with a different stack on r62+, or the same stack with a bitmap under
2 GB.
Related: [[youtube-video-freezes-are-a-software-css-blur-on-the-main-thread]].
