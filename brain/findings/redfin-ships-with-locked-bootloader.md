---
id: redfin-ships-with-locked-bootloader
title: A redfin out of the box has its bootloader locked; first boot needs an unlock
scope: device:google-redfin
subsystem: boot
severity: finding
confidence: proven
evidence: fastboot getvar all on serial 0A31XXXXXX068X (2026-08-31): unlocked:no, secure:yes, secure-boot:PRODUCTION; after fastboot flashing unlock, fastboot getvar unlocked: yes
refutes: a redfin in FASTBOOT being ready for fastboot boot or flash without an unlock
first-learned: 2026-08-31
---

**The question** — why does the redfin in the bootloader refuse fastboot boot
and fastboot flash even though the kernel, the DTS and the boot offsets are all
in place?

**The answer** — a redfin out of the box has its bootloader locked
(unlocked:no, secure:yes, secure-boot:PRODUCTION in fastboot getvar all).
Locked, it refuses both flashing and RAM boot. Unlock it with
fastboot flashing unlock (that wipes the device's data), then check
fastboot getvar unlocked.

**What this rules out** — a broken bootloader, a bad cable, a wrong image:
while it is locked, no image is accepted, and nothing about the image reveals
the state. The check is one getvar, not a rebuild.

**How it was established** — fastboot getvar all showed unlocked:no (serial
0A31XXXXXX068X, 2026-08-31); fastboot flashing unlock answered OKAY and the
device then reported unlocked: yes. A locked redfin accepting a fastboot boot
would overturn it; none should.
