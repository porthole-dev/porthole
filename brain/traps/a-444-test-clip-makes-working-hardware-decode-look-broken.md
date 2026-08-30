---
id: a-444-test-clip-makes-working-hardware-decode-look-broken
title: A 4:4:4 test clip makes working hardware decode look broken
scope: generic
subsystem: video
severity: trap
confidence: proven
evidence: 2026-08-30 taimen, kernel 7.2.2 aport r21. ffmpeg -f lavfi -i testsrc -c:v libx264 (no -pix_fmt) yields yuv444p / High 4:4:4 Predictive. v4l2h264dec advertises profile={baseline,constrained-baseline,main,high,stereo-high,multiview-high} -- 4:4:4 absent -- so accept-caps fails and the pipeline dies 'not-negotiated (-4)' with ZERO HFI traffic; decodebin silently substitutes software. Re-encoded -pix_fmt yuv420p -profile:v high: explicit v4l2h264dec gives 406 tx 0x211005 / 408 rx 0x221008, decodebin autoplugs hardware (408 FILL_BUFFER_DONE), and v4l2vp9dec gives 313.
first-learned: 2026-08-30
---

**Generate your test clip with `-pix_fmt yuv420p`, or you will spend an evening
debugging a decoder that was never broken.**

`ffmpeg -f lavfi -i testsrc -c:v libx264` with no pixel format produces
**yuv444p / High 4:4:4 Predictive**, because `testsrc` emits RGB and libx264
keeps the chroma resolution. No mobile video engine decodes 4:4:4. Venus is
right to refuse it, and every layer above reports that refusal in a way that
looks like a different, much scarier bug:

    v4l2h264dec, forced   -> "not-negotiated (-4)", ZERO HFI traffic
    decodebin             -> silently uses software, ZERO HFI traffic
    ffmpeg h264_v4l2m2m   -> frame= 0 from 900 packets, no error at all

The last one is the really dangerous one. Its HFI trace shows 16 EMPTY_BUFFER
sent and 16 EMPTY_BUFFER_DONE returned with zero FILL_BUFFER -- which reads
exactly like "the firmware consumed the input and produced nothing", the
signature of the unclocked-subcore bug fixed in aport 0194. It is not that. The
client never queued output because negotiation had already failed upstream.

**The tell is in the caps, and it is one grep:**

    gst-launch-1.0 -v filesrc location=clip.264 ! h264parse ! fakesink \
      | grep -oE 'profile=.string.[a-z0-9:-]*'
    -> profile=(string)high-4:4:4      <- not in the decoder's list

    gst-inspect-1.0 v4l2h264dec | grep -A2 profile
    -> baseline, constrained-baseline, main, high, stereo-high, multiview-high

**With a correct clip everything works**, on the same boot, minutes apart:

    -pix_fmt yuv420p -profile:v high
      explicit v4l2h264dec : 406 tx 0x211005, 408 rx 0x221008
      decodebin           : autoplugs HARDWARE, 408 FILL_BUFFER_DONE
      v4l2vp9dec          : 313 FILL_BUFFER_DONE

**Why this earns a note rather than an apology:** the false result is coherent
at every level. Ranks look right (v4l2 elements at primary+1 = 257, above every
software decoder), the device node is correct, the firmware is healthy, and the
fallback is *silent by design* -- autoplugging is supposed to try the next
element without complaining. Nothing anywhere says "your clip is 4:4:4". The
only thing that catches it is checking what the codec-under-test was actually
handed.

**The generic lesson:** a synthetic test input is part of the code under test.
Before concluding hardware is broken, prove the input is something that hardware
was ever supposed to accept. `ffprobe -show_entries stream=profile,pix_fmt` is
cheaper than the evening.
