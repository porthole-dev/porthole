---
id: no-hardware-video-decode-is-built
title: There is no hardware video decode on taimen -- venus is described in DT but not built
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: config-postmarketos-qcom-msm8998-6.18.aarch64 line 2717 reads "# CONFIG_VIDEO_QCOM_VENUS is not set"; msm8998.dtsi:4032 has venus: video-codec@cc00000, compatible qcom,msm8998-venus, with clocks/GDSC/interrupt wired; firmware-google-taimen packages no venus blob
refutes: video playback is slow because the GPU or CPU underperform; venus is unsupported on msm8998 mainline; enabling venus alone will give Firefox hardware decode
first-learned: 2026-08-28
---

> **SUPERSEDED IN PART, 2026-08-29.** The headline is no longer true: hardware
> H.264 and VP9 decode were brought up on this device and verified bit-exact.
> What survives is the narrower claim -- that venus being described in DT does
> not mean decode is built, and that enabling it alone does not give Firefox
> hardware decode. See [[venus-decode-works-and-what-it-took]].

**The question** — YouTube in Firefox is very slow. Is that the GPU?

**The answer** — no. **No hardware video decoder is built into the shipping
kernel at all**: `# CONFIG_VIDEO_QCOM_VENUS is not set`. Every frame is decoded
on the CPU -- VP9 or AV1, which is what YouTube serves by default -- and then
composited to a 1440x2880 panel. Four A73s can do that, but not comfortably,
and it is the one workload that will pin the gold cluster.

**The hardware is already described.** `msm8998.dtsi:4032` carries
`venus: video-codec@cc00000`, `compatible = "qcom,msm8998-venus"`, with its
clocks, the `VIDEO_TOP_GDSC` power domain and its interrupt. What is missing is
only the config symbol and the firmware: `BLOBS.md:34` lists
`venus.{mbn,mdt,b00..}` -> `qcom/venus-*/` as present in the vendor image, and
`firmware-google-taimen` does not package it.

**Do not assume enabling it fixes Firefox.** Firefox uses VA-API on Linux;
venus is a V4L2 stateful decoder with no VA-API driver, and Firefox's V4L2 path
is experimental. Venus would reach GStreamer and mpv well before it reaches
Firefox. Enabling it is necessary, not sufficient, for the reported symptom.
This half is reasoned from the interfaces, not measured on the device.

**What helps today, at no cost** — make YouTube serve H.264 instead of
VP9/AV1, which is dramatically cheaper to software-decode:
`media.mediasource.webm.enabled = false` in Firefox's about:config. Capping
playback resolution helps again, since the panel is 1440x2880 regardless.
