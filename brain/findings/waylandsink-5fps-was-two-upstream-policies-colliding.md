---
id: waylandsink-5fps-was-two-upstream-policies-colliding
title: The waylandsink 5 fps cap was two upstream policies colliding -- wlroots hides LINEAR from v3 clients, GStreamer refuses INVALID
scope: device:google-taimen
subsystem: display
severity: finding
confidence: proven
evidence: WAYLAND_DEBUG protocol trace + /proc wchan sampling + caps logs, 2026-09-01 evening; fixed by pmaports temp/phoc 0003 (pkgrel 51), caps verified populated on the patched compositor
refutes: phoc starves waylandsink of buffer releases; the cap is a wayland round-trip limit; waylandsink is slow at rendering; the dmabuf handoff itself is broken; venus degraded over the day
first-learned: 2026-09-01
---

**The question** — `waylandsink` delivers 5 fps (35 with sync=false) where GL
sinks do 60 ([[waylandsink-delivers-5fps-where-other-sinks-do-60]]). The
handoff's fork: if wl_buffer.release lags, it is compositor-side; if prompt,
it is inside waylandsink. Which?

**The answer** — Neither. Releases return in ~3 ms and the sink is idle
~225 ms between commits; the frames never reach the wayland path at speed
because **playsink silently inserted a software videoconvert**: one spinning
thread (`vqueue:src`, R in 80/80 wchan samples while every other thread
slept) CPU-converting 1080p at ~4.4 fps. The convert exists because
v4l2h264dec ! waylandsink cannot negotiate dmabuf at all:

1. mesa/freedreno on a5xx reports exactly ONE explicit modifier for all 64
   formats: LINEAR. wlroots therefore holds {LINEAR, INVALID} per format.
2. wlroots' v3 modifier events collapse exactly that set to INVALID-only —
   an XWayland workaround (xorg/xserver#1166, still open) applied to every
   v3 client.
3. GStreamer >= 1.24 waylandsink binds v3 (no feedback support even in
   1.28.5, hardcoded in gstwldisplay.c) and DROPS INVALID by policy
   ("we prefer disabling zero-copy over risking a bad output").
4. ∅ dmabuf formats → `drm-format=(int){ }` (visible with
   GST_DEBUG=waylandsink:6) → CPU convert → 4-35 fps, and with sync=true
   basesink discards the late frames, which is the 5 fps.

The GL sinks never consult the wayland format list — they import dmabufs
through EGL, which happily takes implicit modifiers. That is why they were
always fine and why the corruption GStreamer fears does not occur here.

**The fix** — pmaports `temp/phoc` patch 0003 (pkgrel 51) drops the
workaround branch in the bundled wlroots: the modifier set on this GBM is
explicit-LINEAR-capable, so xserver#1166 (GBM without modifier support) does
not apply. Verified on the patched compositor: every format now sends
0x0 + INVALID, and waylandsink's display caps list all formats.
Upstream-bound: wlroots should gate the workaround on a GBM that actually
lacks modifier support; GStreamer should bind dmabuf v4 and read the
feedback table.

**What this rules out** — instrumenting phoc's buffer releases (prompt);
QoS/clock tuning on the sink (symptom); "venus degraded over the day"
(118 fps to fakesink at the same moment the sink did 4.4); adding queues or
converters (playsink already had one — it IS the problem).

**Instrument traps** — measured rates for the same pipeline varied 4.4-35
fps with CPU frequency and logging overhead, all of it the speed of the
hidden videoconvert, none of it the sink; a busy-spinning streaming thread
shows wchan 0/R, so sample stat AND wchan together; the greeter's compositor
(phrog) never maps foreign toplevels, so a pipeline "plays" against it with
the panel at 0 vsync/s — panel-on (bl_power=0) plus a vsync delta is the
only honest confirmation, and the backlight must be checked, not assumed.
