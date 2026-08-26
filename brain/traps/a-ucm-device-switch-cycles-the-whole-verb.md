---
id: a-ucm-device-switch-cycles-the-whole-verb
title: A UCM device switch cycles the whole verb, so a DisableSequence runs mid-use
scope: generic
subsystem: audio
severity: trap
confidence: proven
evidence: On taimen (2026-08-26) the loudspeaker button during a call produced androidboot.bootreason=watchdog with no ramoops. Logging from inside the UCM sequences themselves -- `exec "/bin/sh -c 'echo VERB-DISABLE >> /tmp/ucmlog'"` in each -- showed that switching from "Voice Call (Earpiece)" to "Voice Call (Speaker)" runs VERB-DISABLE, VERB-ENABLE, DEV-SPEAKER. The verb's DisableSequence zeroed the two DPCM routing mixers, stopping both AFE ports underneath an ADSP vocproc still rendering into them.
first-learned: 2026-08-26
---

ALSA's use case manager reads as two levels: a **verb** (the mode) and
**devices** within it (which output). It is natural to assume that changing the
output runs a device sequence and leaves the verb alone.

It does not. PipeWire's card-profile layer (ACP, inherited from PulseAudio)
models each `(verb, device)` pair as its own **card profile** — you can see
them in `pactl list cards` as `Voice Call (Earpiece)` and `Voice Call
(Speaker)`. Changing the output is therefore a *profile* change, and a profile
change dismantles the current verb and sets the new one. **The verb's
DisableSequence runs every time the user presses the loudspeaker button.**

That is harmless if the verb sequences only flip codec switches, which is why
the PinePhone gets away with it — its Voice Call verb has no DisableSequence at
all, and each device's Disable only turns its own amplifier off. It is not
harmless if the verb owns something with a lifetime: a route that something is
streaming through, a PCM held open, a DSP session set up out of band.

Two consequences worth remembering:

1. **Put teardown on the transition that really ends the mode**, not in the
   verb's DisableSequence. Ending a call means going back to HiFi, so HiFi's
   EnableSequence is where the call route gets dropped.
2. **Nothing whose lifetime spans the mode may be released by the verb.** A
   process holding a PCM open for the duration of a call has to be stopped from
   the other verb's EnableSequence for the same reason.

## The instrument

You do not have to guess which sequences run. alsa-lib supports `exec` and
`shell` inside a sequence (syntax 3, alsa-lib >= 1.2.5), and PipeWire really
runs them:

    EnableSequence [
        exec "/bin/sh -c 'echo VERB-ENABLE >> /tmp/ucmlog'"
    ]

Put one in every sequence, switch profiles, read the file. Diffing `amixer
contents` across the switch is not enough on its own: the disable and the
re-enable cancel out, so a control that was zeroed and restored looks untouched.
