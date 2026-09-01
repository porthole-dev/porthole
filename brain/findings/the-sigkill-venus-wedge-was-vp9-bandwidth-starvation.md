---
id: the-sigkill-venus-wedge-was-vp9-bandwidth-starvation
title: The "SIGKILL wedges venus until reboot" was VP9 bandwidth starvation misread -- venus survives SIGKILL on both codecs
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-09-01 night, same boot the wedge was reported on (no reboot in between). 10x H.264 + 2x VP9 randomized mid-decode SIGKILLs each followed by a verified full decode; hfi_trace of the "dead" VP9 session shows 349 FILL_BUFFER_DONEs; VP9 fakesink decode completes in 33.8 s (17.8 fps) with mas_venus voting 125 kBps avg / 0 peak
refutes: a SIGKILL mid-decode wedges venus until reboot; systemd-oomd killing the browser poisons the firmware for the boot; the driver leaks fw sessions on abnormal close; venus needs a session-cleanup fix for dirty teardown
first-learned: 2026-09-01
---

**The question** — the display handoff's §2.5: after systemd-oomd SIGKILLs
Epiphany mid-decode, "the node still answers ioctls and advertises VP90, but
no session yields a frame while software decode of the same file succeeds"
until reboot. What does the driver mishandle in a dirty teardown?

**The answer** — nothing. On the SAME boot that claim was written about
(never rebooted since), venus decodes VP9 to fakesink start to finish. The
teardown path is robust: ten randomized mid-decode SIGKILLs on
`v4l2h264dec` and two on `v4l2vp9dec`, each immediately followed by a
complete verified decode, produced zero failures, and hfi_trace shows the
killed sessions closing with the same SYS_PC_PREP/done tail as a clean EOS.

What LOOKED like a dead decoder is **VP9 running below realtime on the
1 MB/s DDR fallback vote**: the missing msm8998 bandwidth table means
load_scale_bw() votes `kbps_to_icc(1000)` — read back live as
`mas_venus 125 avg / 0 peak` — and 1080p30 VP9 decodes at **17.8 fps**
(600 frames in 33.8 s) while H.264 does 122 fps on the same vote. Under a
sink clock, QoS discards everything late, the video region freezes, and a
30 s probe timeout reads as "no frames". The hfi_trace of one such "dead"
session contains 349 FILL_BUFFER_DONE messages: the firmware was answering
the whole time.

**The fix is the bus vote, now verified live** — the already-written aport
patch `0203-media-venus-msm8998-vote-the-bus-for-the-decode-load.patch`
(the linux-ws WIP; not this session's authorship). The tree-built
venus-core with that table, pushed and running: `mas_venus` votes
**580000** during 1080p30 VP9 (the exact table row) and the same 600-frame
clip decodes in ~12 s (**~50 fps**, was 17.8). H.264 unchanged at ~121 fps,
and VP9 survives mid-decode SIGKILLs on the new module too.

**DIVERGENCE HAZARD**: the phone now runs a TREE venus-core while the aport
kernel does not carry 0203 — any `fast`/`kernel` build or kernel apk
upgrade silently reverts VP9 to the 1 MB/s vote. Land 0202/0203 in the
aport series (the APKBUILD edit staging them is already in the pmaports
working tree) before the next kernel build.

**Instrument traps** — a probe timeout is not a wedge verdict: pair it with
hfi_trace (are FILL_BUFFER_DONEs flowing?) and the interconnect summary
before declaring the fw dead. And a "confirm the wedge" experiment run
after another suspected wedge, on the same boot, inherits the earlier
state: this one was reported wedged and measurably was not, so whatever the
morning observed had already cleared or was this same starvation.
