---
id: call-audio-needs-a-voice-service-the-kernel-does-not-have
title: Call audio is silent because mainline has no voice service — not UCM, not the sound server, not the modem
scope: soc:msm8998
subsystem: audio
severity: finding
confidence: proven
evidence: taimen docs/CALL-AUDIO-2026-08-25.md — measured on the running kernel: /proc/asound/pcm lists only MultiMedia1/2; 0 of 1251 mixer controls match voice/cs-voice/vmmode; kernel config has QDSP6 COMMON/CORE/AFE/ADM/ROUTING/ASM and no Q6VOICE, no VOICEMMODE
refutes: it is a UCM problem, the UCM Voice Call verb will fix it, it is a PulseAudio vs PipeWire problem, the sound server is holding the card, it is earpiece vs loudspeaker routing, it is a modem fault, it is a regression from an earlier call, it is bad state from a previous session
first-learned: 2026-08-25
---

**The question** — an outgoing call connects and holds, SMS works, and there is
no audio in either direction. Loudspeaker changes nothing. What is broken?

**The answer** — nothing is broken. Call audio was never implemented. On
Qualcomm the modem's voice audio goes modem ↔ ADSP ↔ codec over SLIMbus and the
AP's only job is to set that session up. Mainline has no voice service to set it
up with: no `Q6VOICE`, no `VOICEMMODE`, and **0 of 1251** mixer controls
matching `voice`/`cs-voice`/`vmmode`. There is no voice backend, no session, and
no PCM the AP could loop instead.

**This is a feature to implement, not a bug to fix**, and the missing piece is
kernel-side.

## What this rules out

Each of these was measured and none of them is the cause:

- **"The UCM has no Voice Call verb, add one."** True, and necessary, and
  nowhere near sufficient — there is no voice path to route *to*. **Do not start
  by writing UCM.** This is the single most attractive wrong turn here, because
  the UCM file is short, ours to edit, and visibly missing the verb.
- **"PulseAudio and PipeWire are fighting over the card."** They were, and it
  was worth fixing, but the failure is *upstream of the sound server* — it fails
  identically once one stack owns the card cleanly.
- **"It is earpiece versus loudspeaker routing."** `EnableSpeaker` *fails*;
  both are silent because nothing could be routed at all.
- **"The modem."** SMS works, the call connects and stays active.
- **"A regression, or bad state from an earlier call."** A clean install fails
  identically.

## The size of the real work, measured

| | |
|---|---|
| downstream `q6voice.c` | 8,699 lines |
| downstream `q6voice.h` | 1,905 lines |
| plus | `msm-pcm-voice-v2.c`, `msm-pcm-host-voice-v2.c`, `voice_params.h` |
| mainline voice service (`VSS_`/`MVM`/`CVS_`/`cvp_`) | none |
| mainline AFE loopback / port-to-port | none |

Two routes, both substantial: implement q6voice/VoiceMMode properly, or get the
modem to expose its voice audio as a PCM and loop it in userspace — which it
does not do here today.

## How it was established, and what would overturn it

Read `taimen/docs/CALL-AUDIO-2026-08-25.md` — it carries the logs, the probe
table and the config evidence in full. This note is the pointer, not a copy.

It would be overturned by a mixer control or PCM appearing that carries a voice
backend — so re-check `/proc/asound/pcm` and the `voice` mixer grep after any
kernel config or driver change, rather than assuming this is still true.

Related: [[read-the-vendor-before-inventing-a-mechanism]] — downstream's
`msm-pcm-voice-v2.c` defines `snd_pcm_ops` with no `.pointer` and no `.copy`,
because nothing ever transfers through that PCM. Inventing a timer for it
instead of reading it produced a hard lockup on a live call.
