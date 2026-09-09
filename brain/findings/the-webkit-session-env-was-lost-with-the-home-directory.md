---
id: the-webkit-session-env-was-lost-with-the-home-directory
title: The browser's whole WebKit environment lived in ~/.config and a rootfs reflash deleted it, which reads as four separate new bugs
scope: generic
subsystem: graphics
severity: finding
confidence: proven
evidence: taimen 2026-09-09, kernel 7.2.2 #32, webkit2gtk-6.0 2.52.6-r63. `systemctl --user show-environment` listed GSK_RENDERER, VK_LOADER_DRIVERS_DISABLE and GST_PLUGIN_FEATURE_RANK (all from /usr/lib/environment.d, so the instrument sees environment.d) and NO WEBKIT_ variable at all; `ls ~/.config/environment.d` -- the directory did not exist. Home was recreated Sep 5 05:57 by the 2026-09-04 rootfs reflash. The four settings had been hand-written to ~/.config/environment.d/50-webkit-skia-cpu.conf on 2026-09-02.
refutes: the scroll artefacts, the white half-screens, the slow page loads and the video judder are four separate regressions; a browser that starts is a browser configured correctly; environment.d in $HOME is a place to ship a setting
first-learned: 2026-09-09
---

**The question** — a user reports four things at once about the browser: scroll
artefacts, half the screen going white during a fast scroll and slowly filling
back in, pages taking a long time to render, and video judder that heats the
phone. Which of them is which bug?

**The answer** — none of them. All four are one missing file.

`~/.config/environment.d/50-webkit-skia-cpu.conf` carried the whole WebKit
session configuration. A rootfs reflash recreates `/home`, so it went, and
nothing announced it: the browser still starts, and every one of these
settings is a *default* WebKit is perfectly happy to fall back to.

| gone | what WebKit then does | what the user reports |
|---|---|---|
| `WEBKIT_SKIA_ENABLE_CPU_RENDERING=1` | rasterises with Skia on the GPU: on an a540 the blur passes fault, the kernel resets the GPU, and phoc has to rebuild its renderer for every client | "scrolling artefacts", corruption in other apps' icons |
| (the same line) | 877-912 MB of DRM memory instead of 339 MB, inside a scope capped at `MemoryHigh=1500M` -- so the browser sits under memory pressure, and WebKit answers memory pressure by dropping its tile cover multiplier from 2.0 to **1.0**, which is no prepaint beyond the visible rect at all | "half the screen is white, then it fills back in" |
| (the same line) | swap, direct reclaim, oomd | "pages take a while to render" |
| `WEBKIT_GST_VIDEO_DECODING_LIMIT=2560x1440@60` | accepts 2160p60 from YouTube on a 1440x2880 panel and decodes every one of those pixels to throw three quarters away | "video lags and the phone gets hot" |
| `WEBKIT_LAYERS_TILE_SIZE=1440x1024` | the default tile heuristic; measured worse here by 2x on video frame time | (folded into the above) |

**What this rules out** — that these are four regressions. Before checking the
environment, each one has an obvious and *wrong* place to start looking: the
compositor for the artefacts, the tile code for the white areas, the network
for the load times, venus for the video. Check the environment first. It costs
one command and it is the difference between a day and a minute.

**Also ruled out**: that a browser which starts, maps a window and renders a
page is a browser running the configuration you shipped it. Every symptom here
came from a perfectly healthy-looking browser.

**How it was established** — `systemctl --user show-environment` is the whole
instrument, and it carries its own positive control: it printed `GSK_RENDERER`,
`VK_LOADER_DRIVERS_DISABLE` and `GST_PLUGIN_FEATURE_RANK`, which come from
`/usr/lib/environment.d`, so it demonstrably reflects environment.d -- and it
printed no `WEBKIT_` variable. `~/.config/environment.d` did not exist.

**The fix, and why there**: the four lines now ship in the *device package*, as
`/usr/lib/environment.d/60-taimen-webkit.conf` (`device-google-taimen` r41).
`~/.config` is not a place to ship a setting a device is not correct without --
it is a place for a user to override one. [[a-launch-that-skips-the-user-manager-loses-environment-d]]
is the sibling trap: even with the file present, a launch that does not go
through the user manager reads none of it.

**What would overturn it** — a session where `show-environment` lists the
WEBKIT_ variables and the symptoms are still there. That is a different bug and
this note does not cover it.
