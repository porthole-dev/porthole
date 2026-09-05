---
id: the-frame-period-is-quantised-so-sub-refresh-wins-are-invisible
title: Epiphany's frame period is pinned at 2 refreshes because the CPU paint alone exceeds one -- every sub-quantum optimisation measures as neutral
scope: device:google-taimen
subsystem: graphics
severity: finding
confidence: proven
evidence: three builds x three arms, 2026-09-05, playback p50 34.3 / 33.8 / 34.0 ms for r53, r55 (damage cull) and r56 (+clip cull); uprobe budget 19.0 ms cpu paint + 13.3 ms wait; 240 fps capture showing a modal hold of exactly two 16.67 ms refreshes
refutes: cutting GPU work speeds up the browser; pipelining the compositor reaches 60fps; the damage flip and tile culling failed because they do not work
first-learned: 2026-09-05
---

**The question** — a long series of compositor optimisations on this port have
all measured neutral: the GPU clock, `skip-empty-tiles`, the damage-clipping
preference flip (8% on a synthetic arm, nothing on Epiphany), and then two
tile culls written specifically to cut GPU work. Why does nothing move?

**The answer** — because the frame period is **quantised to the panel refresh,
and the CPU paint alone already exceeds one refresh.**

The budget, from uprobes:

    cpu paint (renderLayerTree)   19.0 ms
    wait for frameDone (GPU)      13.3 ms
    period                        32.5 ms   = 2 x 16.67 ms refresh

One refresh is 16.67 ms. The paint is 19.0 ms **on its own**. So:

- Take the GPU to **zero** and the period is still 19 ms, still misses the
  refresh, still presents on every second one. No change.
- **Pipeline** the two halves perfectly and the period is max(19.0, 13.3) =
  19.0 ms. Still over 16.67. Still 2 refreshes. No change.
- Cut GPU work by a third, as the tile culls do, and 32.5 becomes ~30. Still
  2 refreshes. **Invisible.**

Measured directly, three arms per build:

| build | playback p50 | scroll p50 |
|---|---|---|
| r53 | 34.3 ms | 34.2 ms |
| r55 damage cull | 33.8 ms | 34.8 ms |
| r56 + clip cull | 34.0 ms | 33.8 ms |

**What this rules out** —

- **"The culls do not work."** Not established. They are sub-quantum, which is
  a different statement: r55's run-to-run spread widened markedly (p50
  31.5-34.7, frames-over-budget 29-73%) where r53 is tight (34.2-34.4,
  65-69%), which is what a patch whose benefit depends on the page looks like.
  They were never instrumented to prove they fire, which is the gap to close
  before judging them.
- **"Cutting GPU work will speed up the browser."** It cannot, alone. The CPU
  half sets the floor.
- **"Pipelining the compositor reaches 60 fps."** It cannot either, alone, for
  the same reason -- and see
  [[the-compositor-period-is-cpu-paint-plus-gpu-tail-serialized]] for why it
  was tempting.

**What it means for the work** — to gain anything at all you must cross 16.67
ms, which needs **both** halves addressed together: the paint under ~16 ms
AND the GPU tail overlapped or removed. Nothing incremental pays.

That makes the DPU plane path for video the only lever with the right shape:
it removes the video from the page composite entirely rather than making the
composite cheaper, so the page stops being repainted at video rate at all --
`waylandsink` plays the same 4K60 clip with the GPU parked at 257 MHz and the
die flat at 53-56 C, against 710 MHz and 77 C here. For scrolling, the target
is the 19 ms paint itself: 315 layers and five tree walks
([[epiphanys-frame-is-20ms-of-compositor-cpu-plus-a-10ms-gpu-tail-not-a5xx-batches]]).

Measure with at least three arms per build --
[[one-arm-cannot-resolve-a-browser-change-here]].
