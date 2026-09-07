---
id: the-lock-says-who-not-what
title: The lock says who has the device, never what the device is doing
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: porthole tools/ph-device.sh; taimen AGENTS.md §3.1, 2026-08-19
first-learned: 2026-08-19
---

A mutex over a physical device serialises *access*. It knows nothing about
*state*. Those are two different questions and conflating them wastes whole
slots.

**The worked example.** An agent queued ten minutes for the phone, got the lock,
and was killed at its own ceiling having done nothing — because another agent
had left the phone in the bootloader, so there was never an ssh to make. Nothing
in the lock knew that. Two agents lost their slots that day.

Declare the state you need and fail in a second instead:

```sh
TK_AGENT=<you> tools/ph-device.sh --need-booted   ssh ...       # exit 76 if not
TK_AGENT=<you> tools/ph-device.sh --need-fastboot fastboot ...  # exit 76 if not
```

Exit **76** means wrong state. It is deliberately distinct from **75** (could
not get the lock) because the two need opposite responses: 75 is retryable by
waiting, and **waiting will never fix a 76** — something has to physically move
the device first.

The state is checked *before* queueing and *again* after the lock is taken,
because the previous holder may have moved the device while you waited. The
observed state is recorded in the holder file, so the next agent's timeout
message says what the device was doing rather than merely who had it.

**And the etiquette: if you find the device in a state you did not put it in,
say so and hand back.** Do not recover someone else's experiment out from under
them — a device sitting in the bootloader is often a measurement in progress.

Related: [[usb-ids-cannot-tell-booted-from-bootloader]],
[[exit-codes-are-an-api]].
