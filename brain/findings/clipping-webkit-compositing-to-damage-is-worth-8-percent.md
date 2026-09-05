---
id: clipping-webkit-compositing-to-damage-is-worth-8-percent
title: Clipping WebKit's compositing to the damaged rectangles is worth ~8% and 3 C on taimen, not a step change
scope: device:google-taimen
subsystem: graphics
severity: finding
confidence: proven
evidence: MiniBrowser --features A/B, 4K60 YouTube, 60 s per arm, 2026-09-04; commit p50 38.7/37.6 -> 35.7 ms, die at saturation 80.3/81.6 -> 77.0 C; WEBKIT_SHOW_DAMAGE screenshots
refutes: the full-viewport recomposite is what makes Epiphany hot; UseDamagingInformationForCompositing is a neutral pref; Rectangles mode floods the system compositor with damage rects
first-learned: 2026-09-04
---

**The question** — Epiphany recomposites the whole viewport for a playing
video: `WEBKIT_SHOW_DAMAGE=1` tints the entire screen red, and the die climbs
47 -> 73 C in 30 s. WebKitGTK collects damage and hands the system compositor a
tight rectangle, but `UseDamagingInformationForCompositing` defaults **false**
everywhere, so it never clips its own TextureMapper compositing, and
`UnifyDamagedRegions` defaults **true** on GTK, collapsing a frame's damage to
one bounding box. Fix the two together and the browser should stop painting
pixels it did not change. How much is that worth?

**The answer** — the flip does exactly what it says, and buys **~8% of the
compositor's frame period and ~3 C**. It is a real, reproducible improvement
and it is not the fix for the heat.

MiniBrowser exposes the prefs through a **hidden** `-F/--features` flag (it is
in `--help-all`, not `--help`), so this is a runtime A/B and needs no 5.5 h
webkit rebuild to answer:

    MiniBrowser --features=+UseDamagingInformationForCompositing,-UnifyDamagedRegions

60 s of steady 4K60 YouTube playback, arms either side of a reboot:

| | base | base2 | +clip,-unify |
|---|---|---|---|
| own `wl_surface.commit` p50 | 38.7 ms | 37.6 ms | **35.7 ms** |
| p90 | 44.6 | 44.2 | **41.3** |
| worst frame | 162.5 | 138.9 | **111.8** |
| die at saturation | 80.3 C | 81.6 C | **77.0 C** |
| GPU frequency | 710 MHz, 30/30 samples | 710, 30/30 | 710, 30/30 |

The GPU never leaves its top OPP in either arm. That is the shape of the
result: less area painted, the same number of passes and the same layer walk,
which is what [[epiphanys-frame-is-20ms-of-compositor-cpu-plus-a-10ms-gpu-tail-not-a5xx-batches]]
and [[webkits-frame-loop-is-one-frame-in-flight-and-a5xx-is-batch-bound]]
already said the cost was.

**What this rules out** —

- **"The whole-viewport recomposite is why the phone gets hot."** It is worth
  3 C at saturation. The die still saturates around 77 C on a video, still on
  a pinned 710 MHz GPU. The structural fix is still to get the video off the
  GL compositing path and onto a DPU plane, which is what
  `waylandsink` does at 53-56 C with the GPU parked at 257 MHz
  ([[taimen-heat-is-epiphany]] territory).
- **"`UseDamagingInformationForCompositing` is a neutral pref."** It is
  neutral *on its own*, which is what the 2026-09-02 measurement found, and
  the reason is not that WebKit ignores it:
  `TextureMapperLayer::collectDamageSelf()` marks any layer holding a contents
  buffer -- the decoded video layer -- fully damaged every frame, so under
  Unified the bounding box already **is** the viewport. Both prefs have to
  move or neither does anything.
- **"Rectangles mode will flood phoc with `wl_surface.damage` calls."** It
  does not. The `WAYLAND_DEBUG=1` log shows **exactly one
  `wl_surface.damage_buffer` per commit in both arms** (1581 commits / 1581
  damage_buffer on base, 1681 / 1681 on the flipped arm). The two damage
  consumers do not need decoupling: WebKit still unifies what it sends
  outward and only clips what it paints itself.

**How it was established** — `WEBKIT_SHOW_DAMAGE=1` screenshots are the
control that says the flag did anything: with defaults the entire viewport is
uniformly tinted; with the flip the tint is a set of discrete rectangles with
untouched regions between them (the YouTube top bar, the bottom bar, the right
column). The numbers come from the browser's own commit intervals parsed out of
`WAYLAND_DEBUG`, not from phoc's output rate, and every arm proves the video is
advancing before it measures.

Two controls that had to be added before any of it was true:

- An arm behind the phosh lockscreen renders nothing, so the video never
  starts and the numbers are of a still page --
  [[an-arm-behind-the-phosh-lockscreen-measures-a-still-page]].
- The YouTube consent sheet is modal and swallows `movie_player.playVideo()`.

It would be overturned by a measurement on a page whose damage is genuinely
small and scattered -- this one is dominated by a video layer that is honestly
fully damaged every frame, which is the least favourable case for the flip.
Shipped as `damage-clip-compositing-on-gtk.patch` in `temp/webkit2gtk-6.0`.
