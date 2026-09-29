---
id: dtbo-package-padding-hides-correct-overlay
title: Packaged DTBO has a correct payload but a different digest
scope: device:google-taimen
subsystem: boot
severity: trap
confidence: proven
evidence: 2026-09-29 local device-google-taimen 1-r65 APK had a 2048-byte boot/dtbo.img with SHA-256 d3d8cc74; its first 364 bytes had the expected fd9752b3 digest, and the remaining bytes were zero. The 1-r68 APK contains exactly 364 bytes with SHA-256 fd9752b381403312bcdeda8bb8555f0ad964f3464e7bc4ed307e3cef018362da.
first-learned: 2026-09-29
---

**Symptom** — The Taimen DTBO source appears correct, but the packaged
`/boot/dtbo.img` fails the image export's exact digest check.

**Cause** — `mkdtboimg cfg_create` writes the 364-byte DTBO table and pads the
file to its 2048-byte page size. The header's total-size field is big-endian:
`d7 b7 ab 1e 00 00 01 6c` starts with the magic and declares 364 bytes.
Reading that field with a native-endian `od -tu4` produced 1,812,004,864 on
the little-endian build host and once made a sparse file of that size.

**What to do** — Keep the table bytes declared by the header, parse its size
as big-endian, and reject an invalid magic or size before packaging. Check the
APK's extracted DTBO length and SHA-256, not only the DTS or build log.
