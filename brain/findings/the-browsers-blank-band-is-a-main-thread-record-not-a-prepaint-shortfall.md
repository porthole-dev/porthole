---
id: the-browsers-blank-band-is-a-main-thread-record-not-a-prepaint-shortfall
title: The unpainted band during a fast fling is a 200 ms record() on the WebKit main thread, and no prepaint knob bridges it
scope: generic
subsystem: graphics
severity: finding
confidence: proven
evidence: taimen 2026-09-09, kernel 7.2.2 #32, webkit2gtk-6.0 2.52.6-r63/r64, mesa 26.1.6-r14, phoc 0.57.0-r62. tools/repro/scroll-blank/arm.sh, one 6-fling drag, phase uprobes armed before launch. YouTube watch page: record 1063-2954 ms of an 11.4 s window, p90 2.8-135 ms, max 186-198 ms; longest contiguous blank band 0.66-0.68 of the viewport; client worst frame 204-222 ms; compositor 59.8-63.8 fps with ZERO dropped frames in every arm. Wikipedia 79000 px: record 291 ms, max 44.5, band 0.096. Layout is 4 calls / 4 ms per Wikipedia drag. WEBKIT_SKIA_RECORD_SPLIT_RATIO off/2/1.2 normalised per 1000 px scrolled: 113/148/134 ms of record, jank 25/21/23. WEBKIT_LAYERS_COVER_LEAD 0.5 vs 0.85, three arms a side: 20.7 janks either way. WEBKIT_LAYERS_COVER_MULTIPLIER 2 -> 4 removed every >50% frame, 2/2 arms, at 740 -> 1415 MB of DRM memory. --kiosk-mode (no chrome, so no toolbar resize) still produced a whole-viewport blank.
refutes: the blank areas are dropped frames; the compositor cannot keep up; the tile cover multiplier is mis-set; WEBKIT_SKIA_RECORD_SPLIT_RATIO helps on a heavy page; putting the prepaint budget ahead of the scroll instead of around it helps; the browser chrome resizing the web view causes the whole-viewport blanks
first-learned: 2026-09-09
---

**The question** — "I scroll fast and half the screen goes white, then the
content slowly fills back in." Which part of the pipeline is late?

**The answer** — none of it is *late*. The compositor delivers every frame on
time; the frames have nothing in them. One `CoordinatedPlatformLayer::record`
call of 186-200 ms blocks the WebKit main thread while the scrolling thread
keeps scrolling at 60 Hz, and the backing store has no tiles for the area that
has just come into view.

    page                  record max   longest blank band   compositor
    Wikipedia, 79000 px      44.5 ms          0.096         0 dropped
    YouTube watch page      198.0 ms          0.683         0 dropped

The client's worst frame in the same arm is 204-222 ms, which is that record
plus the frame it delayed. `record()` walks the render tree for the dirty
bounding box, on the main thread, holding the layer lock.

**Why no prepaint knob fixes it** — the prepaint budget at the default cover
multiplier of 2.0 is half a viewport ahead of the scroll. A fling travels more
than that in 200 ms. So the question is not where the budget sits, it is how
big it is:

| knob | what it does | result |
|---|---|---|
| `WEBKIT_SKIA_RECORD_SPLIT_RATIO` 2, 1.2 | record dirty tiles separately when the bounding box is mostly clean | null, per 1000 px scrolled: 113 / 148 / 134 ms |
| `WEBKIT_LAYERS_COVER_LEAD` 0.85 | the same budget, ahead of the scroll instead of around it | null, 20.7 janks either way at n=3 |
| `WEBKIT_LAYERS_COVER_MULTIPLIER` 4 | **triple** the budget | works -- and costs 740 -> 1415 MB of tile memory against a scope capped at 1500M |

The one that works is the one that buys more tiles, and it cannot be afforded.

**What this rules out** — that the blank areas are dropped frames (zero, every
arm); that the compositor is the problem (59.6-63.8 fps throughout); that the
layout storm is still happening (4 calls, 4 ms per Wikipedia drag, against 289
calls and 1629 ms before the r61 patches); that the browser chrome resizing the
web view is behind the whole-viewport blanks (`--kiosk-mode` removes the chrome
and still produces them); and both prepaint knobs above.

**How it was established** — `tools/repro/scroll-blank/arm.sh`: the longest
contiguous flat BAND in the frame (not the total flat fraction, which on a text
page is mostly paragraph gaps), the client's own commit record, and uprobes on
the render phases, in one arm that refuses to report unless the page moved.
Every arm reads back the WEBKIT_ variables from `/proc/<web process>/environ`,
because [[the-webkit-session-env-was-lost-with-the-home-directory]] is what
this campaign started as.

**What would overturn it** — a build where `record` is off the main thread, or
a page where the band appears with `record` under ~50 ms. Both would say the
mechanism above is not the whole story.

**Where it goes next** — upstream. Recording a display list for a scrolled
layer is main-thread work in every port; the GTK port has no incremental path
for it. The cheap local mitigations are exhausted.
