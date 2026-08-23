---
id: device-google-taimen
title: Google Pixel 2 XL (taimen, MSM8998) — the reference port
scope: device:google-taimen
subsystem: overview
severity: fact
confidence: proven
evidence: the taimen repo — docs/BLUEPRINT/, docs/campaign/, docs/HANDOFF-*.md
first-learned: 2026-07-24
---

The port porthole was extracted from, and the profile that proves the framework
is real rather than aspirational.

## Why it is worth reading even for a different device

Taimen is a 2017 Snapdragon 835 phone with an A/B bootloader, a DSC panel, a
Google-specific coprocessor (Easel / Visual Core), and no mainline support
worth the name at the start. Most of what went wrong is not specific to it —
that is the entire premise of `brain/laws/` and most of `brain/traps/`.

The distinctively taimen-shaped parts, kept honest in
`profiles/google-taimen/tools/`: Easel, the FTM4 touch controller, Qualcomm
chromatix camera tuning, the TAS smart amp, and the device's mic topology.

## Where the evidence lives

The full corpus stays in the taimen repo — roughly 600 KB of it — because copies
rot against their originals. This brain cites it; it does not duplicate it.

| what | where |
|---|---|
| Subsystem deep-dives (suspend, perf, display, camera, wifi, pstore, reset) | `taimen/docs/BLUEPRINT/BP-*.md` |
| Per-workstream campaign status | `taimen/docs/campaign/` |
| Session handoffs, ~20 | `taimen/docs/HANDOFF-*.md` |
| What works, with evidence | `taimen/docs/DEVICE-SUPPORT-MATRIX.md` |
| Build procedure and its traps | `taimen/docs/BUILD-RUNBOOK.md` |
| Device state protocol | `taimen/docs/DEVICE-PROTOCOL.md` |

## Hardware facts as data

`profiles/google-taimen/device.env` — the forbidden slot, the boot-retry
countdown, the watchdog ceiling, the USB gadget that lies, the bootimg offsets.
Each of those was expensive to learn and is now one `porthole config` away.

## Lineage

Preceded by an angler (Nexus 6P, MSM8994) attempt, archived. The most durable
thing carried forward from it was not code: it was the habit of not trusting the
instrument.
