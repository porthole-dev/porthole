---
id: the-compositor-period-is-cpu-paint-plus-gpu-tail-serialized
title: Epiphany's frame period is CPU paint PLUS the GPU tail, serialized -- frameDone to next frame start is 0.1 ms
scope: device:google-taimen
subsystem: graphics
severity: finding
confidence: proven
evidence: ph-webframe.sh uprobes on webkit2gtk 2.52.6-r52 (build-id 4735460...), 316 frames of 1440p YouTube playback, 2026-09-04; corroborated by a 240 fps camera capture and by wl_surface.commit intervals. NOTE the first run was accidentally on Skia-GPU rasterisation -- see the caveat at the end
refutes: the compositor is waiting on vsync; the compositor is idle between frames; damage clipping is the big win; frame-done-at-fence already releases the compositor thread
first-learned: 2026-09-04
---

**The question** — Epiphany delivers about half the frames it decodes. WebKit's
own counters say it decoded 58 fps and dropped 1 frame in 3095, yet the panel
gets a new video frame every 33-50 ms. Where do the frames die, and is the
compositor slow or is it waiting?

**The answer** — it is waiting, and it is waiting on itself. The budget, from
uprobes on `ThreadedCompositor::renderLayerTree` entry and return,
`didRenderFrame`, `frameDone`, plus the msm submit/retire tracepoints:

                                    CPU raster      GPU raster
                                    (the session's) (accidental)
    period                          p50=32.5 ms     31.6
    cpu paint (renderLayerTree)     p50=19.0 ms     17.9
    gpu submit->retire span         p50=27.0 ms     26.4
    gpu retire after paint end      p50=12.0 ms     12.4
    paint end -> frameDone          p50=13.3 ms     13.4
    frameDone -> next frame start   p50= 0.1 ms      0.1

**The arithmetic closes with nothing left over**: 19.0 + 13.3 + 0.1 = 32.4 ms
against a measured period of 32.5 -- and 17.9 + 13.4 + 0.1 = 31.4 against 31.6 on
the other. Both rasterisation modes, same structure. And the last row is the whole finding --
**0.1 ms** from `frameDone` to the next paint starting. The compositor is not
idling, not waiting for vsync, not waiting for the page. It restarts the
instant the GPU retires, because it may not start sooner: one frame in flight.

So the period is `cpu + gpu`, not `max(cpu, gpu)`. Pipeline the two and it
becomes ~19 ms -- **30 fps to ~52 fps**, essentially panel rate.

The second run also ended with the **GPU at its 257 MHz idle clock** and still
paid the same 12 ms retire-after-paint. That 12 ms is submit round-trip, not
GPU load, which is why raising the GPU clock has always measured neutral here.

31.6 ms also lands just under the 2-refresh boundary (33.3 ms), which is why a
240 fps capture of the panel shows a modal hold of exactly 2 refreshes (47 of
93 holds) and never a single-refresh update.

**What this rules out** —

- **"The compositor is waiting for vsync."** 0.1 ms. It waits for nothing.
- **"The compositor is slow."** The paint is 17.9 ms, which would sustain 56
  fps on its own. It is the serialisation that costs the other half.
- **"Damage clipping is the big win."** It shrinks the *paint* half only. That
  is why flipping the damage preferences measured 8% and not more
  ([[clipping-webkit-compositing-to-damage-is-worth-8-percent]]): it optimised
  one half of a serialized pair, and the period stayed on the same 2-refresh
  boundary.
- **"frame-done-at-fence.patch already fixed this."** That patch is in the
  installed r52 and is described as releasing the web process at the render
  fence. Whatever it releases, the compositor thread is still gated: the 0.1 ms
  gap is measured on a build that carries it.

**Caveat, now resolved** — the first run of this was launched with
`systemd-run` from ssh, which does **not**
inherit `~/.config/environment.d`, so `WEBKIT_SKIA_ENABLE_CPU_RENDERING=1` was
lost and the arm ran on **Skia-GPU rasterisation** -- the exact trap in
[[a-launch-that-skips-the-user-manager-loses-environment-d]], walked into while
investigating. It faulted the a540 twice and aborted phosh twice. The
**structure** is unaffected -- `frameDone -> next frame start = 0.1 ms` is a
scheduling property, not a rasterisation one -- but the 17.9 / 13.4 ms split is
a GPU-raster split. It was re-run with the guards set explicitly and both
columns are above: the numbers barely move and the 0.1 ms gap is identical.
Any arm here must still set `WEBKIT_SKIA_ENABLE_CPU_RENDERING=1` and
`WEBKIT_SKIA_CPU_PAINTING_THREADS=2` explicitly, because the accidental run
faulted the a540 twice and aborted phosh twice
([[phosh-aborts-on-a-gpu-reset-and-takes-the-session-with-it]]).

**How it was established** — `tools/ph-webframe.sh`, whose uprobe offsets are
valid only for one build; the installed library's build-id was checked against
the one in the script header **before** the run, and matched. The page was
proven to be playing (2560x1440, hd1440) before any number was recorded. Die
at 74.5 C with the GPU pinned at 710 MHz, so the 12.4 ms GPU tail is a
hot-clocked figure rather than a best case.

Two independent instruments agree with it: the browser's own
`wl_surface.commit` intervals (p50 36-39 ms) and a 240 fps camera capture of
the panel (modal hold 33 ms). Overturned by a build where
`frameDone -> next frame start` is materially above zero.
