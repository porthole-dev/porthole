---
id: gtk4-icon-squares-are-the-gsk-renderer-not-mesa
title: GTK4 symbolic icons drawn as solid squares on a5xx are GSK_RENDERER=gl, not mesa
scope: soc:msm8998
subsystem: graphics
severity: finding
confidence: proven
evidence: "taimen 2026-09-16, mesa 26.2.2-r51 installed and mapped by both phoc and the app, obscura 0.2.0-r9. Six consecutive `grim` captures of the header under GSK_RENDERER=gl were byte-identical solid squares (no flicker); the same binary in the same session under GSK_RENDERER=ngl and under GSK_RENDERER=cairo rendered alarm-symbolic and preferences-system-symbolic correctly. All seven icon names resolved on disk (`find /usr/share/icons -name '<name>.svg'`), Adwaita carrying 588 symbolic icons."
refutes: the header icon squares are the a5xx S-order GMEM bin corruption; mesa r51 is missing or not installed; adwaita-icon-theme is missing so the icons do not resolve
first-learned: 2026-09-16
---

**The question** — symbolic icons in a GTK4 app's header bar draw as solid
white quads on an Adreno 540. Some icons in the same header are fine. Is this
the known a5xx GMEM bin-order corruption coming back?

**The answer** — no. It is the **GSK renderer**. `GSK_RENDERER=gl` (the legacy
GL renderer) draws certain symbolic icons as filled quads on a5xx; `ngl` and
`cairo` both draw them correctly. On taimen the `gl` pin came from the device
package's `taimen-gsk.conf`, so every GTK4 app on the device inherited it.

The icons that survived are the ones compiled into GTK/libadwaita itself
(`open-menu-symbolic`, the MenuButton chevron). The ones that failed come from
the on-disk Adwaita theme (`alarm-symbolic`, `preferences-system-symbolic`).
That split is a useful tell: it looks like a theme problem and is not one.

**What this rules out** —

- **Not the a5xx S-order GMEM bin corruption.** That one *flickers*: it scored
  23/42 and 18/22 square frames, and was fixed in mesa pkgrel 51. Here six
  consecutive captures were byte-identical. **Stable squares are not that bug.**
  Check for flicker before reopening any GMEM theory.
- **Not a missing or outranked mesa.** r51 was installed, and `/proc/<pid>/maps`
  showed both phoc and the app mapping the installed `libgallium-26.2.2.so`.
- **Not a missing icon theme.** Every icon name resolved to an SVG on disk.
  A GTK4 icon that fails to *load* renders as the `image-missing` glyph, not as
  a filled quad — a solid block means it rasterised and composited wrong.

**How it was established** — hold the app and session fixed and vary only
`GSK_RENDERER`, relaunching via the session's own launcher and confirming the
value actually applied by reading `/proc/<pid>/environ` (it is easy to relaunch
into a *reused* window and measure nothing). Capture the same screen region
several times per renderer: flicker vs. stability is the discriminator that
separates this from the GMEM bug.

Note `GSK_RENDERER` may live in the **systemd user manager's** environment
(`systemctl --user show-environment`), seeded from `/usr/lib/environment.d`, so
it survives an app restart and is not visible in any shell rc file.

What would overturn it: icons rendering as solid quads under `ngl` too, or the
same squares appearing with the GPU soft ISP disabled — neither was seen.

Related: the mesa a5xx raster-bin-order fix is a genuinely separate bug and
stays fixed; this finding does not reopen it.
