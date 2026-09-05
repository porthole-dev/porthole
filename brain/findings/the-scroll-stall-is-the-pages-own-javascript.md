---
id: the-scroll-stall-is-the-pages-own-javascript
title: The browser scroll stall is the page's own JavaScript -- not the engine, not the tile-record path, and not page settling
scope: generic
subsystem: browser
severity: finding
confidence: proven
evidence: tools/repro/scroll-record/arm.sh, four arms 2026-09-05 -- live article 270-289 performLayout calls per 10 s drag (1551/1629 ms, max 500-537 ms); the same article with its five <script> tags stripped, images intact, 9 calls (11 ms); a plain static page 6 calls (4 ms)
refutes: a settled scroll fires none of the six rendering phases; the 250-500 ms performLayout calls are page-settling work rather than scroll work; the tile-record path is the remaining main-thread ceiling; the scroll cost is diffuse with no hot spot
first-learned: 2026-09-05
---

**The question** — Epiphany scrolls a long Wikipedia article on a phone and the
WebProcess main thread sits at ~50 % of a core with multi-hundred-millisecond
stalls. A flat `perf` profile shows no hot spot (top main-thread symbol
`memset` at 0.81 %). Which phase is it, and is it the engine or the page?

**The answer** — the page's own JavaScript, and it is style and layout.

Uprobes on the six rendering phases plus the tile-record path, one 10 s drag on
a settled page, `?useformat=desktop`, two arms:

    phase            calls   p50 ms   p90 ms   max ms  total ms
    updateRendering    328      5.3      8.2    942.6      3790
    layout             289      1.7      2.8    537.0      1629
    style               23      1.2     41.1    377.5       789
    record             312      0.8      1.5     36.5       434

A settled scroll relayouts on 88 % of its rendering updates, and the tail is a
**full-document style recalc and relayout of a 79 000 px article** -- one 537 ms
layout, one 377 ms style, inside one 942 ms rendering update. That tail is the
felt stutter; the p50 is fine.

Serve the same article back from `127.0.0.1` with only its five `<script>` tags
removed -- same CSS, same 38 images, same markup -- and it collapses:

    | arm                        | layout calls | layout ms | style calls | style ms | main thread |
    |----------------------------|--------------|-----------|-------------|----------|-------------|
    | live article               |          289 |      1629 |          23 |      789 |       47.7% |
    | same article, scripts gone |            9 |        11 |           4 |        5 |       22.1% |
    | plain static page          |            6 |         4 |           0 |        0 |       17.4% |

Images are not it: the scripts-gone arm still loads all 38 of them.

**What this rules out**

- **"A settled scroll fires none of the six rendering phases."** It fires all of
  them, hard. The arm that reported zero was measuring a **blanked screen** --
  the drag moved the page zero pixels. See
  [[an-injected-touch-does-not-wake-a-blanked-screen]]. A null needs a positive
  control; this arm forces a synchronous layout in the *same* browser instance
  after the drag, so "no hits" can be told apart from "never attached".
- **"The 250-500 ms `performLayout` calls are page-settling work."** They happen
  20 s after the page settled, during the drag.
- **"The tile-record path is the remaining ceiling."**
  `CoordinatedPlatformLayer::record` is 434 ms of a 10 s window, a ninth of the
  rendering total, p50 0.8 ms. It is not the ceiling and never was.
- **"There is no hot spot."** There is; a flat profile cannot see it because the
  shipped build has no frame pointers, so no call graph resolves and 1.6 s of
  layout spreads over thousands of leaf symbols. Uprobes on phase entry points
  see it immediately.
- Engine, compositor, GPU, display path and clocks are all exonerated for this
  workload: the identical drag on a static page of the same height costs the
  main thread 17 %.

**What it does NOT explain** — the residual tail. With every script gone the
client still presents 21-29 frames per drag late, max commit interval 240 ms,
on a page whose main thread is nearly idle. That residual is ours and is what a
next campaign should chase -- and `plain.html` is a far better instrument for it
than a live site, because nothing in it can dirty layout.

**How it was established** — `tools/repro/scroll-record/arm.sh` (offsets are
recomputed per build by `tools/tk-wkoffsets.sh`; the arm refuses if a browser is
already running, because a uprobe does not attach to an already-mapped library).
Two live-article arms agree to within 7 % on every number above. It would be
overturned by a scripts-stripped arm that still relayouts, or by finding that
stripping `<script>` also removed markup that mattered -- the mirror is a byte
copy with only `<script>...</script>` deleted: 73442 px against the live
page's 73963 px in the arm beside it.
