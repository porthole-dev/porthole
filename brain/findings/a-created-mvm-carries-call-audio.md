---
id: a-created-mvm-carries-call-audio
title: A (created) MVM carries call audio: what matters is whether the modem had a call, not the joined flag
scope: soc:msm8998
subsystem: audio
severity: finding
confidence: proven
evidence: 2026-08-27 call: mvm=0x0026 (created), audible both ways, confirmed by the tester and by the carrier
refutes: a session logged as (created) instead of (joined) is the silent case; (joined) is the marker of an audible call; check the joined flag before believing a call result
first-learned: 2026-08-27
---
**The question** — the driver logs `voice session up: mvm=0x0026 (created)` and
the earlier notes say an audible call is the one that reads `(joined)`. Is a
`(created)` MVM a regression to chase before trusting anything else in the run?

**The answer** — no. On 2026-08-27 a carrier call came up with

    voice session up: mvm=0x0026 (created) cvs=0x0100 (created) cvp=0x0100,
                      rx=0x1006 tx=0x4001

and it was audible in **both** directions -- the tester heard the carrier, and
the carrier heard the microphone. The `(joined)` label is not the thing that
decides.

What decides is what the older trap actually measured: **whether the modem had
a call of its own when the session was made**. A session forced open while the
modem is idle gets a handle from a different range (`0x0020`) and is silent for
the rest of the boot; a session made during a real call gets `0x0026` and
carries audio, whether the ADSP answered the CREATE with EALREADY (`joined`) or
allocated it (`created`).

So the useful check on a call result is **the handle and the modem's state**,
not the flag. `taimen-voicehold` gating on an ACTIVE call is still exactly
right, and is why this call got `0x0026` at all.

**What this rules out** —

- **"`(created)` means no audio, stop and reboot."** It does not. Two hours
  were nearly spent treating a working call as a regression on the strength of
  that word.
- **"The flag is still lying, as it did before patch 0177."** No: 0177 made
  only a CREATE able to set it, and it is accurate here. A CREATE that really
  did allocate a fresh session reports `(created)`, correctly -- the session is
  simply not silent for that reason.

**How it was established** — one live carrier call, both directions confirmed
by ear (the tester's, and the far end's report). The trace either side of it is
in `logs/2026-08-27-slow/` and the session line above is from `dmesg`. What
would overturn it: a `0x0026 (created)` call that IS silent, which would mean
the handle range is not the discriminator either.
