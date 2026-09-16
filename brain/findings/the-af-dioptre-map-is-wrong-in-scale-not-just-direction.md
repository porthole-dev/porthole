---
id: the-af-dioptre-map-is-wrong-in-scale-not-just-direction
title: "The imx362 AF tuning map is wrong in scale: code 0 is ~0.47 m, not 14 cm, and infinity lands near code 157"
scope: device:google-taimen
subsystem: camera
severity: finding
confidence: proven
evidence: "taimen 2026-09-16, libcamera 99990.7.2-r20, kernel 7.2.2, bringup/camera/af-sweep.py. Three sweeps that passed all three controls (actuator readback tracked every commanded code; exposure held to 0% spread with AeEnable false; scene stable). Lit book square-on, laser 468 mm +/-7: every tile peaks at code 0, best tile 9.3x. Room, shelving 2-3 m: tiles peak at code 128, up to 11x. Book on a far shelf: strongest tile peaks at code 192, 15.5x. Hall readback: code 0 = +330, code 581 = -268, tracking the commanded value to within a few counts."
refutes: the lc898214xd exposed range 0..581 spans infinity to macro; FOCUS_ABSOLUTE code 0 is infinity as the driver comment says; the tuning file map [0.0,581,7.0,0] is usable; a flat focus sweep means the actuator is not moving
first-learned: 2026-09-16
---

**The question** — autofocus "does not work properly" on this device. Is the
dioptre-to-code map in `/usr/share/libcamera/ipa/simple/imx362.yaml` right?

**The answer** — the map's **direction is right** and its **scale is badly
wrong**. Measured, a farther subject means a higher `V4L2_CID_FOCUS_ABSOLUTE`:

| subject | distance | sharpest at |
|---|---|---|
| lit book, square on | 0.47 m | code 0 (9.3x) |
| room, shelving | ~2-3 m | code 128 (up to 11x) |
| book on a far shelf | far | code 192 (15.5x) |

That the map is wrong is measured. The exact replacement is not yet: a fit
through the two solid points gives roughly **74 codes per dioptre** and puts
**infinity near code 157**, not 581 — but that is an extrapolation from two
points and wants more stations before anything is written into a tuning file.
Three consequences follow either way:

- Roughly **two thirds of the exposed range sits beyond infinity** and does
  nothing. Sweeps look flat from about code 300 upward in every run.
- The **near end is unreachable**. Code 0 measures ~0.47 m while the map claims
  it is 7 dioptres (14 cm) and the vendor XML claims `MinFocusDistance 0.1`.
  A subject at 0.155 m stayed flat to within 5% across the whole sweep, with no
  peak anywhere — the lens cannot get there, so AF has nothing to climb.
- Every reported dioptre is off by roughly 3x, so manual focus and the distance
  an app displays are both wrong.

**What this rules out** —

- **The driver comment is inverted.** `lc898214xd.c` says `0 -> +324
  (infinity)`. Measurement says code 0 focuses NEAR. Do not trust that comment.
- **A flat sweep does not mean a stuck actuator.** The hall readback tracked
  every commanded code to within a few counts across the full span (+330 to
  -268). The flatness at 0.155 m is the subject being closer than the lens can
  focus, not a dead coil. Read the hall before blaming the hardware.
- **The 0..581 range is not infinity-to-macro.** Whatever the vendor's
  `af_tuning` infinity `+324` / macro `-257` means, it does not describe what
  the optics do here.

**Still open** — the vendor numbers cannot be reconciled with the measurement
unless either those two fields were read swapped out of
`libactuator_lc898214xd.so`, or the hall scale is not the DAC scale. The
polarity was deliberately NOT flipped in the driver: the tuning map has to move
in the same change, and the fit needs stations at *measured* distances in good
light. Every dark run cost signal — the lit station gave 9-15x where gain-8.0
runs gave 3x.

**How it was established** — `bringup/camera/af-sweep.py`, which carries three
positive controls, each of which caught a real error the day it was written:

1. **Actuator readback** per step — proves the lens moved, not just that the
   register accepted a write.
2. **Exposure readback** from request metadata — a sweep that silently keeps
   auto-exposing measures exposure, not focus, because the metric divides by
   the mean.
3. **Scene stability** from the laser and the frame mean — two runs were voided
   because the phone was being repositioned mid-sweep, and would otherwise have
   produced a plausible-looking curve.

It measures a grid of tiles rather than one centre patch, so a scene with depth
becomes an asset: near and far tiles peak at different codes, which fixes the
direction without needing a perfectly staged single-distance target.

One more trap worth naming: the viewfinder stream here is **packed ABGR8888**,
not a luma plane. A sharpness metric that walks consecutive *bytes* differences
colour channels inside one pixel and is not a focus measurement at all. Step by
bytes-per-pixel and sample one channel.
