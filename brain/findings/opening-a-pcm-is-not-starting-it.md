---
id: opening-a-pcm-is-not-starting-it
title: Opening a PCM is not starting it: the codec only moves data at TRIGGER_START
scope: generic
subsystem: audio
severity: finding
confidence: proven
evidence: kprobe on slim_stream_enable, A/B on the VoiceMMode1 uplink of a Pixel 2 XL (msm8998, kernel 6.18 #90) -- prepare fires nothing, snd_pcm_start() fires it with nothing ever read; then a live carrier call, audible both ways with no second stream
refutes: the voice uplink needs an ADM copp / an ASM client on the TX port; the vocproc is missing a device-config, a SET_DEVICE_V2 or a voc_state machine; the AFE TX port needs settling time
first-learned: 2026-08-27
---

**The question** — something opens a PCM purely for its side effects (an audio
HAL holding a voice call up, a test holder powering a route, a daemon keeping a
port alive) and the path still carries no audio. Every state you can read says
it should: DAPM shows the widgets on, the DSP port is started, the session is
created with the right port ids, the mixer taps read live signal. Starting a
*second*, ordinary stream on the same back-end fixes it, which makes it look
like the second stream provides something the first cannot.

**The answer** — it provides `SNDRV_PCM_TRIGGER_START`. A codec that carries
its samples on a data bus of its own -- SLIMbus, SoundWire -- enables that bus
from its DAI `.trigger`, not from DAPM and not from `hw_params`. In wcd934x
(`sound/soc/codecs/wcd934x.c`) `slim_stream_prepare()`/`slim_stream_enable()`
appear in `wcd934x_trigger()` and nowhere else in the tree. ASoC DPCM only
propagates a trigger to a back-end when a **front-end is started**, so a stream
that is opened, `hw_params`'d and prepared powers the whole path and moves not
one sample. `snd_pcm_prepare()` is not `snd_pcm_start()`.

Nothing has to be read or written, either: the trigger is the whole of it. A
holder that never transfers is fine -- a holder that never *starts* is not.

**What this rules out** — on the msm8998 voice uplink this killed, in one
measurement, every remaining theory about the ADSP voice session: that the
uplink needs an ADM copp or an ASM client on the TX port; that the vocproc is
missing `voc_set_device_config()`, `VSS_IVOCPROC_CMD_SET_DEVICE_V2` or
downstream's `voc_state` machine; that the AFE TX port needs settling time.
None of them were ever the variable. The voice command sequence was correct the
whole time and the gap was one ioctl in userspace.

More generally: **an "opened but suspended" PCM is a plausible-looking null.**
A sound server that creates a node and leaves it suspended, a UCM verb with a
`CapturePCM` nothing records from, a ctypes holder that stops at
`snd_pcm_prepare()` -- all three read as "the stream is up" in every place you
would look, and none of them transports. If the RX half of a duplex path works
and the TX half does not, check first which half something is actually
*streaming*, before you go looking at the DSP.

**How it was established** — kprobe, so no rebuild:

    echo 'p:slimen slim_stream_enable'   >> /sys/kernel/tracing/kprobe_events
    echo 'p:slimdis slim_stream_disable' >> /sys/kernel/tracing/kprobe_events
    echo 1 > /sys/kernel/tracing/events/kprobes/enable

Four arms on the same front-end, with the route cset by hand so no verb and no
sound server was involved:

| arm | what ran | slim events |
|---|---|---|
| control 0 | nothing open | 0 |
| control 1 | `arecord` on the neighbouring FE (the known-good scaffolding) | enable, disable at stop |
| A | open + `set_params` + `prepare` | **0** |
| B | A + `snd_pcm_start()`, never reading a frame | **enable**, disable at close |
| B' | as B, held 90 s | enable at t, disable at t+90.0 -- no early stop |

Control 0 is what makes A mean anything. A capture-only hold creates no voice
session, so `dmesg | grep -c "voice session up"` stayed 0 and no measurement
after it was poisoned.

Then the fix in userspace -- one `snd_pcm_start()` on the held uplink, and the
throwaway second stream deleted -- and a live carrier call, audible in both
directions.

What would overturn it: a codec whose bus enable sits in `hw_params` or in a
DAPM widget event rather than `.trigger`. Read the codec before assuming; the
lesson is the question to ask, not the answer for every part.
