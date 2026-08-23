---
id: 50-wifi-bt-modem
title: "Playbook: wifi, bluetooth and modem"
scope: generic
subsystem: radio
severity: technique
confidence: proven
evidence: taimen docs/BLUEPRINT/BP-06-wifi-ath10k-rekey.md; docs/HANDOFF-wifi-bt-modem.md
first-learned: 2026-07-29
---

**Goal:** the radios come up, stay up, and survive suspend.

**Done when:** a wifi association survives a rekey and a suspend/resume, and
restarts are zero over a long soak — not merely "it connected once".

## Order

1. **Firmware first, and check ownership not presence.** A firmware file that
   exists is not the same as a firmware file the package recorded —
   [[apk-info-W-wants-the-path-the-package-recorded]].
2. **Bring-up before stability.** Association is the easy half. Rekey, roaming
   and suspend/resume are where the real bugs are.
3. **Soak.** Radio bugs are statistical. A single successful connection tells
   you almost nothing.

## The traps

- [[installing-firmware-can-flash-the-boot-partition]] — installing the firmware
  package can `dd` a boot image, mid-session
- [[dmesg-can-be-empty-about-boot]] — a WARN storm from a radio driver is
  *itself* the thing that destroys your evidence about everything else. If a
  radio driver is logging repeatedly, fix that before you trust any `dmesg` on
  this device.
