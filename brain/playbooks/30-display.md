---
id: 30-display
title: "Playbook: display"
scope: generic
subsystem: display
severity: technique
confidence: proven
evidence: taimen docs/BLUEPRINT/BP-03-display-dsi-dsc.md; docs/HANDOFF-display.md
first-learned: 2026-07-26
---

**Goal:** the panel lights and holds a stable image.

**Done when:** a frame reaches the panel and survives a suspend/resume cycle.

## Order

1. **Panel identification.** Get the vendor DSI init sequence: from the
   downstream kernel's panel driver, from a `dsi-panel-*.dtsi`, or from the
   stock boot image. This is the single highest-value artefact — an init
   sequence you can transcribe beats any amount of guessing.
2. **DSI host before panel.** Confirm the DSI controller and PHY come up before
   blaming the panel driver.
3. **Compression.** If the panel uses DSC, the parameters must match exactly.
   A DSC mismatch produces a lit-but-garbled panel, which reads as a timing
   problem and is not.
4. **Then the compositor.** Only once a frame is provably reaching the panel.

## The traps

- Until this playbook is done, [[never-judge-a-boot-by-the-screen]] governs
  every other playbook. Flip `PORTHOLE_PANEL_STAYS_DARK=0` in the profile when
  it is done — that is a real milestone.
- A panel that lights at the bootloader's timings and dies at yours means the
  bootloader left it initialised. Prove your init sequence runs from a cold
  panel.
