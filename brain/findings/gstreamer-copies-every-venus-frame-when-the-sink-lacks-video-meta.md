---
id: gstreamer-copies-every-venus-frame-when-the-sink-lacks-video-meta
title: GStreamer's V4L2 pool copies every venus frame when the sink offers no video meta: 12 fps at 4K, 82 fps otherwise
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-09-04, kernel 7.2.2 #31 r21, gst-plugins-good 1.28.5. YouTube LXb3EKWsInQ f315 (3840x2160 VP9, 25 Mbit/s): v4l2vp9dec -> fakesink 12.35 fps avg in both mmap and dmabuf capture modes; -> fakevideosink 82.3 fps; ffmpeg vp9_v4l2m2m 69 fps. 1080p60: 46 -> 162 fps. 1440p60: 157 fps either way. GST_DEBUG=v4l2bufferpool:7 logs 'copying buffer / copy video frame' after every capture dequeue at 4K and 1080p, never at 1440p. hfi_trace: the firmware finishes a 4K frame in 12 ms; the 66 ms between EMPTY_BUFFER and the next FILL_BUFFER is the copy.
refutes: venus cannot decode 4k60 VP9 on msm8998; the venus clock or bandwidth tables cap 4K decode; the hardware decoder is slower than software VP9; the 12 fps is a firmware or driver ceiling
first-learned: 2026-09-04
---

**The question** -- can venus on msm8998 decode 4K60 VP9, and why does a
GStreamer decode of a 3840x2160 60 fps stream run at 12 fps with the codec
clock already at its 533 MHz ceiling?

**The answer** -- it can, at 82 fps. The 12 fps is `gst_v4l2_buffer_pool`
copying every decoded frame out of the V4L2 capture buffer. venus emits NV12
with the height aligned to 32 lines (2160 -> 2176, 1080 -> 1088), so the
capture format needs `GstVideoMeta` to describe the padding. A sink that does
not offer `GST_VIDEO_META_API_TYPE` in the allocation query (`fakesink`,
anything that maps raw bytes) cannot take the pool's buffers, so
`gst_v4l2_video_dec_loop()` acquires a downstream buffer and the pool's
process() path goes "buffer not from our pool, grab a frame and copy it".
The copy reads 12.5 MB of uncached DMA memory per frame: ~66 ms. The
firmware needs 12 ms per frame; the two are serialised, hence 12 fps.
1440p has no padding (1440 is a multiple of 32), so it never copies and ran
at 157 fps all along, which is why the earlier "1440p60 is in lockstep with
the panel" measurements looked healthy while 1080p and 4K did not.
`capture-io-mode=dmabuf` does not help: the copy path is the same.

WebKit's video sink advertises video meta and imports dmabufs, so Epiphany
is not on the copying path: YouTube 2160p60 in Epiphany runs venus at
533 MHz with the video element counting ~49 fps and 3 drops per 10 s, while
the page commits at p50 39 ms (the TextureMapper paint, the known ceiling).

**What this rules out** -- the venus clock table (533 MHz was already
selected: the 4K60 load is above the top row and the loop leaves
`table[0].freq`), the bandwidth table (raising the vote from 2.365 to
4.73 GB/s changed 82.3 to 82.5 fps; the row is now in the series as the
honest vote, not as a fix), the firmware, work mode (not sent on the 3XX
HFI), and any "hardware slower than software" reading: software `vp9dec`
does 20 fps at 4K and 39 fps at 1080p on all eight cores.

**How it was established** -- `fpsdisplaysink` with `fakesink` vs
`fakevideosink` on the same clip; `ffmpeg -c:v vp9_v4l2m2m -benchmark` as
an independent client (69 fps); the series' `hfi_trace=1` knob
(`/sys/module/venus_core/parameters/hfi_trace`) timestamping every HFI
packet: with GStreamer, one EMPTY_BUFFER, 66 ms, one FILL_BUFFER, 12 ms,
done; with ffmpeg the cycle is 11.5 ms. `GST_DEBUG=v4l2bufferpool:7` names
the copy. Overturned by: a sink with video meta that still copies, or a
4K decode under 60 fps with no copy in the pool log.

**Measure decode with `fakevideosink`, never `fakesink`.** Clips on the
phone: `/var/tmp/costarica-{4k60,2560x1440p60,1920x1080p60}-vp9.webm`.
