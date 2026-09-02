---
id: youtube-judder-is-2160p60-plus-a-lockstep-decoder-not-venus-throughput
title: YouTube's judder on taimen is 2160p60 being served, plus venus clocked for 30 fps because vdec had no VIDIOC_G_PARM -- not the compositor, not buffer counts
scope: device:google-taimen
subsystem: video
severity: finding
confidence: proven
evidence: 2026-09-02, kernel 7.2.2 #26, phoc r51, webkit2gtk-6.0 2.48.1-r50 then 2.52.6-r50, Big Buck Bunny 60fps 4K on m.youtube.com. vb2/v4l2 tracepoints on /dev/video7 for 4-6 s windows. Unlimited: capture bytesused=12533760 (3840x2176 NV12), venus at 444 MHz, mas_venus 2.3 GB/s, decode 60.0 fps with a 26-32 ms gap every ~200 ms (12 frames), GPU pinned 710 MHz, 70-76 C, DPU 60 vsync/s throughout. With WEBKIT_GST_VIDEO_DECODING_LIMIT=2560x1440@60 (2.52.6): bytesused=5529600 (2560x1440), 60.5 fps, gaps 7 per 5 s, GPU 257-516 MHz, no faults, no oomd. Feeder refills every freed input buffer in 0.8 ms median (never starved); firmware holds each input buffer 31.5 ms (4K) and 32.2 ms (1440p) median with only 2 input buffers in flight and 3-4 capture buffers on the driver side, returned by WebKit exactly one vsync apart (p99 18.8 ms).
refutes: the judder is the compositor or the DPU dropping frames; venus cannot decode 4K60 VP9; the bus vote is wrong; a lower resolution alone makes the cadence perfect; the browser is slow because hardware decode is broken; WebKit holds too few capture buffers; the decoder needs a deeper capture pool
first-learned: 2026-09-02
---

**The question** -- the user sees YouTube freeze for a few ms and catch up,
repeatedly, with the phone getting hot. The panel counter says 60 vsync/s
and `hwdec=yes`. What is stuttering?

**Two things, and the second survives fixing the first.**

**1. YouTube serves 2160p60.** WebKit answers `isTypeSupported` and
`MediaCapabilities.decodingInfo` with "yes" for VP9 at 3840x2160@60, so the
mobile player picks it on a 1440x2880 panel. Venus actually decodes it at
60 fps -- impressive, and irrelevant, because every 12.5 MB frame is then
converted on the GPU (710 MHz, pinned) and composited, which is the heat,
and the working set is ~16 such buffers. 2.52.6 has the knob and 2.48 does
not: `WEBKIT_GST_VIDEO_DECODING_LIMIT=2560x1440@60` (in
`~/.config/environment.d/50-webkit-skia-cpu.conf`) makes both APIs refuse
above it and YouTube drops to 1440p. GPU falls to 257-516 MHz, DRM memory
for the playing tab from ~900 to ~550 MB.

**2. The decoder cannot run ahead of the display.** Read off the vb2 trace:

    OUTPUT refill (done->qbuf)     p50 0.8 ms   the feeder is never short of data
    OUTPUT fw hold (qbuf->done)    p50 32 ms    2 input buffers x 16.6 ms, same at 4K and 1440p
    CAPTURE queued/owned by fw     3-4 / 1-3    WebKit holds the rest
    CAPTURE return (done->qbuf)    p50 6 ms, p99 15 ms  one buffer back per vsync

The firmware gets a capture buffer back once per frame and decodes into it
once per frame. Any single decode longer than a frame period -- a VP9
superframe carries a hidden alt-ref plus the shown frame, two decodes -- has
no slack to hide in and becomes a repeated frame. At 2160p60 that is one
in twelve frames (5 Hz, the "freeze and catch up"); at 1440p60 it is
~1.4/s (p99 32.7 ms). Not throughput: the hold time did not move with a
2.25x smaller frame.

**What 2 actually was** -- the clock. `gst-launch ... v4l2vp9dec ! fakesink
sync=false` on a 1440p60 clip ran at 98 fps with `video_core_clk` at
**269.33 MHz**, and declaring 120 or 240 fps on the caps changed nothing.
strace: GStreamer issues `VIDIOC_G_PARM` on the OUTPUT queue, venus answers
**ENOTTY** -- `vdec_ioctl_ops` has `vidioc_s_parm` but no `vidioc_g_parm`
(venc has both) -- and `gst_v4l2_object_set_format_full` takes that as "no
V4L2_CAP_TIMEPERFRAME" and never calls `S_PARM`. `inst->fps` stays at its
default 30, `load_per_instance` = mbs x 30, and `load_scale_v1` picks the
269 MHz row for anything up to 4K30-equivalent. The firmware then decodes
at about real time by construction, whatever the resolution, which is why
the input hold time was 32 ms at 4K and at 1440p alike.

Fix: 15 lines, `vdec_g_parm` mirroring `venc_g_parm`, series patch 0207
(pkgrel 27, kernel #28). With it: `G_PARM = 0`, `S_PARM` issued, clock
444 MHz for the 1440p60 clip, fakesink ceiling 98 -> 150-160 fps. The
1080p30 clip on the phone is content-heavy (45-55 fps at either clock) and
is not a throughput reference. Upstream-bound; the commit message carries
the numbers.

The "more capture buffers" idea from the first draft of this note is
dead: waylandsink, which pools differently, showed the same 32.5 ms holds.

**What this rules out**
- *The compositor/DPU.* 60 vsync/s during every stutter, 0 underruns,
  drag p99 19.5 ms during playback.
- *Venus throughput or clocks.* 444 MHz, bus voted, 60 fps sustained at 4K.
- *Hardware decode is broken.* `v4l2vp9dec0:src` thread, 4 fds on
  /dev/video7, `hwdec=yes`, WebProcess 17-23% CPU. (It was software in the
  runs launched without environment.d -- see
  [[a-launch-that-skips-the-user-manager-loses-environment-d]].)

**Instrument** -- `echo 1 > /sys/kernel/debug/tracing/events/vb2/enable`
(and `events/v4l2/enable`) for 5 s, then per queue type (9 = capture,
10 = output) compute qbuf gaps, qbuf->buf_done holds, and buf_done->qbuf
refills. Holds tell you who is slow; refills tell you who is waiting.
`bytesused` on capture is the served resolution, exactly.
