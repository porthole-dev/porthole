---
id: the-vocproc-refuses-a-volume-step-without-cal
title: In-call volume: the vocproc refuses VSS_IVOLUME_CMD_SET_STEP without a registered volume calibration table
scope: soc:msm8998
subsystem: audio
severity: finding
confidence: proven
evidence: live carrier call 2026-08-27: 0x000112c2 rejected: 0x1 (ADSP_EFAILED) for steps 25, 50 and 100 with the amp already at 15/15
refutes: Android's in-call loudness is just SET_STEP, send it and the loudspeaker gets louder; the amplifier's DAC gain is the call volume; the loudspeaker is quiet because we do not use the vendor firmware configurations
first-learned: 2026-08-27
---
**The question** — the loudspeaker in a call is audible but far quieter than
Android's, with the amplifier's DAC gain already at 15/15. Where is the rest of
the volume?

**The answer** — a call has two gains in series and this port only has the
second:

    modem -> CVP vocproc [RX volume]  ->  AFE port -> MI2S -> TAS2557 [DAC gain]
                    not implemented                         0-15, the only one we had

Android's in-call slider drives the first: downstream's `Voice Rx Gain`
(`msm-pcm-voice-v2.c:420`) calls `voc_set_rx_vol_step()`, which sends
`VSS_IVOLUME_CMD_SET_STEP` (0x000112C2, `direction = VSS_IVOLUME_DIRECTION_RX`,
`{u16 direction; u32 value; u16 ramp_duration_ms} __packed`) to the CVP.

Implemented and **measured on a live carrier call: the ADSP refuses it.**

    qcom-q6voice: 0x000112c2 rejected: 0x1        (ADSP_EFAILED)

Steps 25, 50 and 100, all rejected, call unaffected. The command's own
documentation says why: the step selects "the best match index in **the
registered volume calibration table**", and nothing in mainline registers one
-- downstream sends `VSS_IVOCPROC_CMD_REGISTER_VOL_CALIBRATION_DATA` out of
ACDB first. `EFAILED` rather than `EBADPARAM` or `EUNSUPPORTED` is the useful
part: the opcode and the payload are right, and the table is what is missing.

So in-call loudness is blocked on **calibration data**, not on the command.

**What this rules out** —

- **"Send the command Android sends and the call gets louder."** Measured, and
  it does not: the DSP refuses it outright.
- **"The loudspeaker is quiet because the port doesn't use the vendor's
  firmware configurations."** It does -- `_s2` and `_s3` are Google's own,
  decoded from `tas2557s_PG21_uCDSP.bin`, the pair `mixer_paths_tavil_taimen`
  selects for handset and speaker-mono. The gap is the DSP's gain, not the
  amp's tuning.
- **"Turn the amplifier up."** It was at 15/15 during the whole measurement.
  That ceiling IS the symptom.

**How it was established** — `Voice Rx Gain` added to q6voice, stepped
0/25/50/100 during a live call while the profile stayed `Voice Call (Speaker)`
and the amp stayed at 15. Every step logged its rejection. What would overturn
it: registering a volume calibration table and getting a different answer --
which is exactly the next experiment, and the reason to keep the control.
