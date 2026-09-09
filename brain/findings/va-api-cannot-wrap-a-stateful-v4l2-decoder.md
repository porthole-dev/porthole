---
id: va-api-cannot-wrap-a-stateful-v4l2-decoder
title: VA-API cannot be put on top of venus: it is a STATEFUL V4L2 decoder and VA-API's model is stateless, which is why only GStreamer-based browsers get hardware decode here
scope: generic
subsystem: video
severity: finding
confidence: proven
evidence: taimen 2026-09-09, kernel 7.2.2 #32. /dev/video7 = qcom-venus-decoder, Capabilities 0x84204000 = V4L2_CAP_VIDEO_M2M_MPLANE|STREAMING|EXT_PIX_FORMAT|DEVICE_CAPS -- no Request API, no V4L2_CAP_IO_MC. OUTPUT accepts whole compressed elementary streams (H264, VP8, VP9, HEVC, VC1G/L, MPG4, MPG2, H263, XVID, all 'dyn-resolution'); CAPTURE produces NV12 and Q08C. `v4l2-ctl --list-ctrls` matches ZERO of sps/pps/slice_param/decode_param/scaling_matrix -- a stateless Request-API decoder exposes all of them. /usr/lib/dri/*_drv_video.so is empty on the device; libva 2.23.0 is installed with no driver behind it.
refutes: libva-v4l2-request can be used with venus; installing mesa-va-gallium gives Adreno a VA driver; Firefox or Chromium can be made to hardware-decode here by supplying a VA-API driver; the missing VA driver is a packaging gap
first-learned: 2026-09-09
---

**The question** — hardware video decode works in Epiphany and in nothing else
on this phone. Firefox and Chromium both want VA-API, and there is no VA driver
here. Can one be written or ported for this decoder?

**The answer** — no, and not because nobody has got round to it. The two models
do not meet.

| | VA-API | venus |
|---|---|---|
| who parses the bitstream | the **client** (ffmpeg, Chromium, Firefox) | the **firmware** |
| what crosses the boundary | `VAPictureParameterBuffer`, `VAIQMatrixBuffer`, `VASliceParameterBuffer` + slice data | a whole compressed elementary stream |
| who owns the DPB | the client, explicitly | the firmware |

A VA driver over venus would have to take the client's *already parsed*
structures and **re-synthesise a conforming Annex-B stream** from them --
rebuild SPS and PPS NAL units, re-emit slice headers bit-exactly -- per codec,
and then reconcile venus's own reference handling with the DPB the client
thinks it is managing. That is why `libva-v4l2-request` supports only
**stateless** hardware (Cedrus, Hantro, rkvdec), where the model maps 1:1, and
why no VA driver for a stateful M2M decoder exists anywhere.

**How to tell which kind you have, in one command:**

    v4l2-ctl -d /dev/videoN --list-ctrls | grep -iE 'sps|pps|slice_param|decode_param'

A stateless decoder exposes those controls. venus matches zero of them, and its
capability word carries no Request API. Its OUTPUT format list is the other
tell: whole codecs (`H264`, `VP90`, `HEVC`, ...) marked `dyn-resolution`, which
only makes sense if something downstream is parsing headers.

**What this rules out** — `libva-v4l2-request` with venus; `mesa-va-gallium` as
a fix (it provides VA-API for Gallium video engines; freedreno has no video
engine at all -- video on this SoC is venus, separate IP); and the idea that
the missing VA driver is a packaging gap rather than an architectural one.

**What the browser choice therefore is**, on any Qualcomm SoC with venus:

1. **GStreamer-based browsers get hardware decode for free** -- `v4l2h264dec`,
   `v4l2vp9dec` and friends are GStreamer's *stateful* V4L2 elements and speak
   venus natively. WebKitGTK (Epiphany) is in this family. Measured on taimen
   2026-09-09: 0 dropped frames in 19 037.
2. **Chromium can, with `use_v4l2_codec=true`.** Chromium already carries a
   maintained V4L2 **stateful** decoder for exactly this hardware class
   (ChromeOS and Android ship it); desktop-Linux builds compile it out. This is
   a build-flag and packaging project, not a driver-writing one -- but it is a
   Chromium build.
3. **Firefox has no path.** Its Linux hardware decode is VA-API only. ffmpeg's
   `h264_v4l2m2m`/`vp9_v4l2m2m` do drive venus, but Firefox's media stack never
   selects them, and wiring it would also need zero-copy import of venus's
   dmabufs or every frame is copied.

**What would overturn it** — a stateless firmware mode for venus (there is
none upstream), or VA-API growing a stateful decode entry point.
