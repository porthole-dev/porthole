---
id: a-hard-hang-writes-nothing-to-disk
title: A hard hang writes nothing to disk — capture on the host, before you trigger
scope: generic
subsystem: diagnosis
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §7
first-learned: 2026-07-30
---

A log file on the device will be empty after a hard hang. The write never
reached storage.

Capture all pre-test state **on the host** before you trigger anything.

Your options for evidence that survives, in order of dependability:

- **Netconsole** — the dependable channel. Transmits over usb0 and wlan0,
  including from NMI context. Prefer usb0: wlan0 WARNs from hardirq.
- **ramoops / pstore** — a *warm* reboot preserves `/sys/fs/pstore/`; a
  power-button reset destroys it. And verify pstore works on your device before
  relying on it: taimen's bootloader is recorded as scrubbing the ramoops region
  anyway.
- **A file on the device** — useless for hard hangs. Fine for anything that
  survives to flush.

Before any unattended hang-producing work, know which of these you actually
have. Discovering you have none *after* the hang costs the whole experiment.

Related: [[watchdog-out-of-range-disarms-instead-of-clamping]],
[[a-journal-grep-matches-your-own-command-line]].
