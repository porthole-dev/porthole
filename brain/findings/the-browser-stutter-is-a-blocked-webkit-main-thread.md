---
id: the-browser-stutter-is-a-blocked-webkit-main-thread
title: The browser stutter is a blocked WebKit main thread, not the display stack
scope: generic
subsystem: browser
severity: finding
confidence: proven
evidence: "taimen 2026-09-02, webkit 2.52.6-r52, kernel #28, phone charging and un-throttled. A setTimeout chain and requestAnimationFrame were sampled in the same page at the same time: rAF max 2762ms big=[2762,2680,505,2587,305], setTimeout max 2291ms big=[1950,2291,1888]. setTimeout needs no compositor, no GPU and no display, and freezes identically. Between stalls p50=16-17ms (true 60Hz). Idle page on the same 315-layer m.youtube.com: p50 17, p99 20, max 22ms, ZERO frames over 33ms."
refutes: "the stutter is TextureMapper/compositor cost; the stutter is thermal throttling; the stutter is GPU autosuspend; the stutter is video resolution or CPU clock; the stutter is swap or cgroup memory.high; the window updates at 15-25 fps"
first-learned: 2026-09-02
---

**The question** — Epiphany playing YouTube stutters on this device. Two
sessions attributed it to the compositor: "the window updates at 12-15 fps",
"the ceiling is TextureMapper compositing 237/315 layers", "~25 ms per
composited frame". Where is the time actually lost?

**The answer** — the frame clock is **60 Hz**. Between stalls `p50 = 16-17 ms`
in every arm ever measured here. What the user sees as stutter is a small
number of **discrete multi-second blocks of the WebKit main thread**, three to
four per 40 s, during media-pipeline activity.

The partitioning instrument is two probes in the same page at the same time:

| probe | needs | max stall |
|---|---|---|
| `requestAnimationFrame` | compositor delivering frames | 2762 ms |
| `setTimeout` chain | **main thread only** | 2291 ms |

`setTimeout` needs no compositor, no GPU, no DPU and no panel. It freezes with
the same period and nearly the same magnitude. **The display stack cannot be
the cause of these freezes.**

**What this rules out** — each measured on this device, not argued:

- **Compositor / TextureMapper / tile size / layer count.** The `setTimeout`
  result above. Also: idle `m.youtube.com` -- the same 315 composited layers,
  same tile size, same page -- runs `p50 17 / p99 20 / max 22 ms` with **zero**
  frames over 33 ms. A compositor that can hold a perfect 60 Hz on the page
  while idle is not what drops 3 seconds while playing.
- **Thermal throttling.** The coolest arm stalled *worst*: 67.7 C, cooling
  state 0 (never engaged), GPU 257 MHz, and a 1221 ms stall. An arm pinned at
  1.03 GHz and actively stepping (74.8 C, cool state 3->6) had **zero** stalls
  over 100 ms. See [[the-msm8998-thermal-trip-is-a-cliff]] -- that bug is real
  and worth fixing, but it is not this.
- **Video resolution.** 1440p / 1080p / 720p: 39.2 / 42.2 / 39.9 fps,
  p50 19.5 / 19.7 / 20.7 ms. Halving the decoded pixels twice changed nothing.
- **CPU clock.** No monotonic relation: 1804 MHz -> 50.2 fps, 2208 -> 43.7,
  1574 -> 39.9, 576 -> 39.2, 499 -> 42.2.
- **GPU runtime-PM autosuspend.** `power/control=on` vs `auto`, alternated
  twice: `runtime_suspended_time` delta was **0 in every arm** -- the GPU never
  suspended during playback, so `autosuspend_delay_ms=250` was never in the
  frame path. Two identical `auto` arms differed more (40 vs 7 stalls) than
  `on` differed from `auto`.
- **Swap and memory pressure.** Across a 40 s window containing three
  multi-second stalls: `pswpin`, `pswpout`, `pgmajfault` **identical** before
  and after; global PSI memory `some total` unchanged. 610 MB was in swap and
  none of it moved.
- **cgroup `memory.high` throttling** (the decaying-penalty shape is its
  signature, so it was checked): `memory.high=max`, `memory.events high=0`,
  `memory.pressure total=0`.
- **JSC garbage collection** as the *large* stalls. `JSC_logGC=1` shows real
  pauses, but bounded: EdenCollection `p=138.59 ms, cycle 211.7 ms`;
  FullCollection `p=92.19 ms, cycle 176.9 ms`. GC explains the 100-400 ms
  tier, not the 2-3 s tier.

**What is established about the block** — the main thread sleeps, it does not
compute. Sampling every thread of every WebKit process at 100 ms while the page
stalled: main thread `__futex_wait` 176 samples and `do_sys_poll` 401 vs `R`
169, with other threads in `R`. Aggregate main-thread state in a separate run
was `S=100%`. So it is waiting on a lock or a peer, with ~23% of samples in
`__futex_wait` against ~28% of wall time lost -- it is blocked, not busy.

The stalls come in a repeating decaying series, e.g.
`rAF 2153, 916, 596, 415 | 2281, 935, 592, 243 | 2139` and
`setTimeout 1428, 657, 487, 265 | 1452, 664, 461 | 1370, 627, 380`,
period roughly 13 s.

They track **media-pipeline activity**, not successful playback: a page whose
player never started (`readyState 0`, `paused`) but which was churning to load
media stalled 3898 ms, while a page whose player sat cued and idle had none.

**Named: the stall is software rasterisation.** Correlating the page's own
stall timestamps with a 50 ms whole-process thread timeline (every thread of
every WebKit process plus phoc, intersected with the stall windows) gives the
same signature in 5 of 5 stalls:

| thread | during stall | baseline |
|---|---|---|
| WebKitWebProcess main | 76-91% | 69.9% |
| **SkiaCPUWorker** | **32-46%** | **10.5%** |
| ThreadedCompositor | 22-36% (*down*) | 54.7% |

`SkiaCPUWorker` runs 3-4.5x above baseline in every stall while the compositor
thread goes *quiet* waiting on it. A large synchronous software raster job is
blocking the main thread. Aggregate sampling cannot see this -- the stalls are
~15% of wall time and idle threads swamp the histogram -- and sampling the
wrong pid is easy: name-matching finds the `bwrap` wrappers, whose main thread
never burns CPU. That mistake produced an earlier, wrong "main thread is
S=100%, blocked on a futex" reading; the page's web process is the one owning a
`ThreadedCompositor` thread.

**And CPU rasterisation is itself a workaround.** It is on because
`WEBKIT_SKIA_ENABLE_CPU_RENDERING=1` avoids a540 rendering faults. Measured
A/B on the same page: GPU rasterisation cuts the worst stall from **2049 ms to
568 ms** with **zero GPU faults**, but renders **visibly corrupt icons** (the
like/save/flag glyphs fill with dithered garbage) with page state held
identical across arms via a DOM `scrollIntoView` before every capture.
`FD_MESA_DEBUG=noubwc` and `sysmem` do not fix it; noubwc makes it worse.

**UBWC is not involved**, despite being an attractive theory for blocky
multicolour garbage: `ubwc_ok = is_a6xx(screen)` in freedreno_resource.c, and
a5xx never registers the modifier `is_format_supported` hook, so a540
advertises linear only and cannot select a UBWC layout. a5xx also cannot render
*to* UBWC -- `fd5_gmem.c` emit_mrt() hardcodes RB_MRT_FLAG_BUFFER to zero,
under a "when we support UBWC" comment. Any A/B of `FD_MESA_DEBUG` on the
browser also cannot explain corruption in the phosh panel, which is a different
process that never saw the variable.

So browser smoothness turns on **correct GPU rasterisation on a540 in
freedreno**, not on a compositor or kernel knob. Until that lands the choice is
correct-and-stuttery (CPU raster) or smooth-and-corrupt (GPU raster).

**Still open** — what holds the lock. Hardware decode is not obviously it
(the software-decode arm stalled too, though that arm is confounded: its video
never played). Next: userspace stacks on the main thread during a stall
(`perf record -g` on the WebProcess, or gdb attach at a stall), and the
GStreamer/MSE append path.

**How it was established, and what would overturn it** — `both_start.js` /
`both_read.js` (rAF + setTimeout in one page, read through the remote
inspector). Any arm that shows `setTimeout` clean while `rAF` stalls would
move the cause back to the compositor. Note that **every browser number taken
before this ran under `WAYLAND_DEBUG=1`** -- see
[[wayland-debug-is-not-a-free-instrument]].
