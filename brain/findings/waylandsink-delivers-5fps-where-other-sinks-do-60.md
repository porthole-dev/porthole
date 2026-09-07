---
id: waylandsink-delivers-5fps-where-other-sinks-do-60
title: waylandsink delivers 5 fps where glimagesink and gtk4paintablesink do 60 -- and WebKit's compositor is the browser's ceiling, not venus
scope: device:google-taimen
subsystem: display
severity: finding
confidence: proven
evidence: 2026-09-01, kernel 7.2.2 with the rd_ptr pageflip patch, sysmem off. Same local 1080p30 H.264 clip, same v4l2h264dec, only the sink varied; DPU vsync counter read as a delta over 6s with playback positively confirmed (process alive, position advancing, decoder runtime_status active). Camera footage of the panel analysed over the video region only. WebKit pipeline read from GST_DEBUG_DUMP_DOT_DIR.
refutes: local playback stutter proves the kernel/display path is broken; the video stutter is a decode problem; the video stutter is GPU capacity; forcing H.264 fixes YouTube smoothness; Epiphany is slow because of our custom WebKit build
first-learned: 2026-09-01
---

**The question** — video and scrolling stutter on this device. Local clips
stutter too, which looked like proof that the kernel/display path was at fault.
Where is it actually lost?

**The answer** — two separate things, and the local-clip "proof" was an artefact
of a broken sink.

**1. `waylandsink` delivers 5 fps.** Same clip, same `v4l2h264dec`, only the
sink changed (1080p30 content, so ~30 vsyncs/s is correct):

| sink | mechanism | panel vsyncs | drops |
|---|---|---|---|
| `waylandsink` | dmabuf -> wayland surface | **5/s** | 9 |
| `glimagesink` | glupload + glcolorconvert | **59/s** | 0 |
| `gtk4paintablesink` | GL texture | **60/s** | 0 |

Fullscreen vs windowed made no difference (5/s vs 7/s), so it is not direct
scanout. The whole stack -- DPU, phoc, venus, the kernel -- sustains flawless
60 fps playback through either GL sink. Use one of those for local playback.

**This invalidated every "clean case" test built on it.** Hours went into the
kernel because a local file "still stuttered" with the browser closed, memory
idle and the GPU at 257 MHz. That was `waylandsink`, not the system.

**The mechanism, isolated.** `waylandsink` is not slow at rendering and it is
not the dmabuf handoff:

| variant | panel vsyncs | drops |
|---|---|---|
| `sync=true` (normal) | 4/s | 8 |
| `sync=false` (no clock scheduling) | **35/s** | **0** |
| forced BGRA copy instead of dmabuf | 6/s | 11 |

`gst_wayland_sink_show_frame` itself takes **45-400 us** and is simply not
being *called* -- basesink discards the frames as late before the sink sees
them. End to end the chain manages ~35 fps while venus alone decodes this clip
at ~114 fps, so attaching the sink costs two thirds of the throughput and
leaves ~17% margin over 30 fps content. QoS then turns any hiccup into a
permanent collapse. The dmabuf path is exonerated (the BGRA copy is equally
bad, and slower still because a 1080p CPU convert is expensive).

**2. WebKit's compositor is the browser's ceiling.** Its pipeline (from
`GST_DEBUG_DUMP_DOT_DIR`) is:

    v4l2h264dec0 -> glupload -> glcolorconvert -> appsink -> WebKit's compositor

Everything up to `appsink` is what `glimagesink` does at 59 fps, so the shared
part is fine. Measured on the panel with a camera, video region only, 24 fps
content: **~10 updates/s, p90 201 ms, max 772 ms**, while YouTube's own counter
reported **0 dropped of 4447** -- the frames are produced and lost after the
video element. Scrolling stutters too, so it is the compositing path, not video.

**Firefox, with no hardware decode at all, scrolls better and degrades
gracefully where Epiphany stutters every time.** Same phone, same compositor.
That is the cleanest statement of the result: WebKit had every hardware
advantage here and still lost.

**What this rules out**

- **The kernel/display path.** 60 fps through two other sinks, and the
  compositor measures 56.7 fps / p50 16.7 ms / 98.8% at 60 Hz under load
  (`profiles/google-taimen/tools/ph-framprobe.py`).
- **Decode.** venus does 1080p30 H.264 at 381% of realtime (VP9: 118%).
- **GPU capacity.** `glimagesink` performs the same 1080p `glupload` +
  `glcolorconvert` at 59 fps. Do not blame the colour-convert pass.
- **Codec.** Forcing YouTube onto H.264 changed nothing measurable
  (9.8 updates/s vs 10.4). Reverted; it also disables VP9 session-wide.
- **Our custom WebKit build.** `webkit2gtk-6.0 2.48.1-r50` differs from stock
  by a 71-line V4L2 sandbox patch and a pmbootstrap configure fix.
  `CMAKE_BUILD_TYPE=Release`, and the `ENABLE_JIT=OFF`/`C_LOOP=ON` block is
  guarded by `riscv64` only, so aarch64 keeps the JIT.
- **Skia GPU vs CPU.** Independent of the stutter: it decides corruption
  (GPU: heavy pages corrupt, and every one of 10 a5xx faults was
  `SkiaGPUWorker`; CPU: clean, 0 faults, half the GEM). Keep CPU. Do NOT also
  set `FD_MESA_DEBUG=sysmem` for the app once phoc is tiling -- blank page.
- **WebKit's dmabuf switches.** `WEBKIT_DISABLE_DMABUF_RENDERER=1` and
  `WEBKIT_DMABUF_RENDERER_FORCE_SHM=1` each render a blank page; the dmabuf
  hypothesis cannot be tested that way.

**How it was established** — DPU vsync counter read as a delta over 6-8 s, with
playback positively controlled every time (process alive, position advancing,
decoder `runtime_status` active) after an early run reported a rate for a
pipeline that had already exited. Camera footage analysed over the video region
alone, because a full-frame diff counts UI damage and a vsync count measures
page redraws, not video updates -- that distinction is what made the browser
look "objectively clean" while the user watched it stutter.

**OVERTURNED THE SAME DAY, by its own clause.** "Evidence that phoc
mishandles the specific buffer protocol both waylandsink and WebKit use"
arrived that evening: phoc advertised implicit-only dmabuf modifiers
([[waylandsink-5fps-was-two-upstream-policies-colliding]], fixed by
temp/phoc 0003/r51), and VP9 was additionally decoding below realtime on
the 1 MB/s DDR fallback vote
([[the-sigkill-venus-wedge-was-vp9-bandwidth-starvation]], fixed by aport
0203). Re-measured on the fixed stack: a bare fullscreen VP9 <video> in
Epiphany runs at **60 panel vsync/s with ~1% WebProcess CPU**, and page
scrolling flings at 0 dropped / 0 jank / max 19.6 ms. Part 2's "WebKit's
compositor is the browser's ceiling" is dead; part 1's sink table remains
valid history for the unpatched stack. The custom-build refutation was also
re-verified at runtime: jsc runs a 30M-iteration loop in 259 ms (JIT
compiled; the riscv64-only C_LOOP block never applies to aarch64, and
cloopfix.patch only fixes compilation of the interpreter file).
