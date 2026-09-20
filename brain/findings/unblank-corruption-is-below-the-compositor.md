---
id: unblank-corruption-is-below-the-compositor
title: The blank/unblank corruption is BELOW the compositor -- grim captures a clean frame while the glass shows garbage
scope: device:google-taimen
subsystem: display
severity: finding
confidence: proven
evidence: ten power-key blank/unblank cycles with dmesg streaming armed -- zero GPU/DPU/DSI errors -- and a grim capture immediately after that renders the app grid, icons and Settings correctly while the operator reports visible corruption on the panel
refutes: the blank/unblank corruption is GPU rendering, the a5xx GMEM patches, mesa, or a driver fault that dmesg would show
first-learned: 2026-09-20
---

# grim is clean. The panel is not. The corruption is downstream of both.

**The reproduction** is the operator's: press power repeatedly to blank and
unblank. `tools/ph-wake-cycle.py 10` does it unattended and reports

    VERDICT survived 10 transitions, 10 with the GPU collapsed, 0 presses missed

**dmesg is silent.** With `dmesg -w` streamed to a file BEFORE the cycles
(systemd-run, per the streaming law), ten transitions produced not one
`a5xx`, `adreno`, `dpu`, `dsi`, `SError`, fault, timeout or underrun line.
This is not a driver fault and nothing will be found by grepping for one.

**And `grim` comes back CLEAN.** A capture taken straight after the ten
cycles, with `bl_power=0`, renders the phosh app grid, every icon and the
Settings list correctly. No tearing, no blocks, no wrong colours.

**That is the finding.** `grim` reads the COMPOSITOR'S output buffer. It does
not read what the DSI panel is showing. A clean capture while the glass shows
corruption puts the fault strictly below the compositor -- in the
DPU -> DSI -> panel path -- and rules out everything above it:

- **Not GPU rendering or mesa.** Measured across the mesa fork rebase; the
  four a5xx patches (`a5xx-raster-bin-order`, `a5xx-tile-parity`,
  `a5xx-tile-init-msaa`, `a5xx-msaa-sysmem`) were restored to the phone in
  26.2.3-r52 and the operator reports the corruption unchanged.
- **Not the GTK renderer.** `gl` and `ngl` both.
- **Not a missing known fix.** TE is wired -- `disp-te-gpios = <&tlmm 11
  GPIO_ACTIVE_LOW>` in msm8998-google-wahoo.dtsi, carrying the gpio11-not-
  gpio10 correction -- and `/sys/module/msm/parameters/cmd_frame_serialize`
  reads `Y` on the running kernel.

**THE INSTRUMENT RULE THIS ESTABLISHES.** For anything visual on this device,
`grim` answers "did the compositor draw it right", never "is the screen
right". When the two disagree the only instrument left is a PHOTOGRAPH of the
panel. Do not conclude "no corruption" from a clean capture; this note exists
because that conclusion was one step away.

**Where to look next**, in the order the evidence supports:

1. The panel's own re-initialisation on unblank. `ph-wake-cycle.py` reports
   the GPU collapsed on every transition, so each unblank re-runs the panel
   power-on and DSC setup. A DSC parameter that is written once at boot and
   not re-written on unblank would look exactly like this.
2. `drm/panel/panel-lg-sw43402.c`'s enable path versus its first-boot path --
   what does boot do that unblank does not?
3. DCS readback after an unblank, which
   [[taimen-dsi-probe-path]] calls the highest-value probe on this panel.
   Compare a register read after boot with one after ten cycles.
