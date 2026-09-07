---
id: touch-drags-scroll-on-the-scrolling-thread-and-r61-removed-the-layout-storm
title: Finger drags on GTK4 WebKit ride the scrolling thread, and r61's two patches cut the Wikipedia drag from 289 layouts per 10 s to 10
scope: device:google-taimen
subsystem: browser
severity: finding
confidence: proven
evidence: 2026-09-06, webkit2gtk-6.0 2.52.6-r61, epiphany 50.6-r50, tools/repro/scroll-record/wkrec.sh with uprobes from ph-wkoffsets.sh on EventHandler::handleWheelEvent, ThreadedScrollingTree::handleWheelEvent, ThreadedScrollingTree::displayDidRefreshOnScrollingThread, ScrollingTreeFrameScrollingNodeCoordinated::repositionScrollingLayers, ScrollingTree::applyLayerPositions and LocalFrameViewLayoutContext::performLayout; 20 drags of 500 ms on the long desktop Wikipedia article. Per thread over 12 s: treeWheel 272 (Core: Scrolling), mainWheel 0, refresh 441, reposition 205 on the scrolling thread + 416 on the main thread, performLayout 10 calls, 63 ms total, max 13 ms. Client: 795 commits, presented on consecutive vsyncs 358/388, 34 frames over 33 ms, max 138 ms. The 2026-09-05 arm on r60 measured 289 layouts / 1629 ms for the same drag.
refutes: the scroll stall is the page's JavaScript forcing 289 layouts per drag; finger drags are handled on the main thread; async scrolling is off on GTK
first-learned: 2026-09-06
---

**The question** -- the 2026-09-05/06 handoffs concluded that the scroll
stall is the page's JavaScript forcing ~290 layouts per drag, and left open
whether the GTK port scrolls a finger drag off the main thread at all.

**The answer** -- it does, and the layout storm was WebKit's, not the page's.
The touch drag becomes wheel events with phases (`WebKitWebViewBase.cpp`),
`EventDispatcher` routes them to `ThreadedScrollingTree::handleWheelEvent`
on the `Core: Scrolling` thread (272 calls, and zero on the main-thread
`EventHandler::handleWheelEvent`), display-link ticks reach that thread
(441) and it repositions the scrolling layers itself (205). On r61 the same
drag that cost 289 layouts on r60 costs 10, because r61 carries two patches:
`dont-relayout-fixed-objects-on-async-scroll.patch` (LocalFrameView::
updateLayoutViewport() re-laid-out every fixed/sticky renderer on every
async scroll step, dirtying the RenderView so the page's next forced layout
was document-wide) and `no-fake-mouse-moves-for-touch-scrolling.patch` (75
hit tests, each forcing a layout, for a hover state no finger has). The page's
scripts still read layout in their scroll handlers; with nothing dirty, those
reads are cheap.

**What this rules out** -- "the scroll stall is the page's JavaScript" as a
verdict that closes the campaign (the JS only paid for what WebKit dirtied);
"async scrolling is off on GTK" (both `AsyncFrameScrollingEnabled` and the
scrolling thread are live); and "r61 was a regression" -- what invalidated
r61 was [[the-webkit-snapshot-crash-is-epiphanys-full-document-thumbnail]].

**How it was established** -- the same uprobe arm as the 2026-09-05 finding,
with the routing functions added and the trace split by thread (the summary
table of `ph-wkphase.sh measure` only knows the fixed phase list; the raw
`/tmp/wk-drag.trace` carries every probe as `name:` / `name_ret:` with the
thread's comm in front). Still open: the residual 34 janks per 10 s (max
138 ms) with the main thread at 27 % and the compositor at 14.5 %, and the
un-measured gaps in `ThreadedScrollingTree`'s desync state machine (layer
positions applied only on display-link ticks; `DisplayLink` sends nothing to
a process without an observer). What would overturn it: the same probes on
r63 showing hundreds of layouts again.
Related: [[the-scroll-stall-is-the-pages-own-javascript]].
