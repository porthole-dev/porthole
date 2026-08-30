---
id: redfin-is-sm7250-not-t48
title: Pixel 5 (redfin) is sm7250/lito, not t48
scope: soc:sm7250
subsystem: bringup
severity: trap
confidence: proven
evidence: Couchpotato-sauce/kernel_google_redbull alpine, arch/arm64/boot/dts/google/lito-v2-redfin-pvt.dts: model "Google Inc. MSM sm7250 v2 Redfin PVT", compatible "google,redfin-sm7250", "qcom,sm7250"; postmarketOS wiki Google Pixel 5 (google-redfin) lists Snapdragon 765G (SM7250)
first-learned: 2026-08-30
---

**Symptom** — the profile says `PORTHOLE_SOC=t48`, porthole reports no sibling on that SoC, and every redfin DTS candidate says `sm7250` or `lito`.

**Cause** — Pixel 5 is not a Tensor device. It uses the Qualcomm Snapdragon 765G, model SM7250, codename `lito`. `t48` belongs to a later Google SoC.

**What to do** — set `PORTHOLE_SOC=sm7250`, use the `lito-*redfin*` DTS files, and do not inherit values from `t48` siblings.

Related: [[a-board-name-is-not-a-soc-name]], [[dtbo-must-match-the-kernel]].
