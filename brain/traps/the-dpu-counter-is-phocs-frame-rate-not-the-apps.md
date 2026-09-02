---
id: the-dpu-counter-is-phocs-frame-rate-not-the-apps
title: The DPU vsync counter is phoc's output rate, not the app's -- a browser scrolling at 30 fps and presenting video at 15 fps both read "60 fps, 0 jank"
scope: generic
subsystem: graphics
severity: trap
confidence: proven
evidence: taimen 2026-09-02, phoc 0.57.0-r51, Epiphany/webkit2gtk-6.0 2.52.6 on m.youtube.com. Same 4-drag run measured two ways: tk-gesture-bench (DPU encoder status deltas) p50 16.6 ms / 0 jank; Epiphany's own wl_surface.commit intervals from WAYLAND_DEBUG=1: p50 32.6 ms, 105 of 219 intervals > 33 ms (default 512 tiles). Idle 1440p60 playback: DPU 59.8/s; wp_presentation_feedback.presented sequence deltas for Epiphany's toplevel 3-5 vsyncs (mostly 4 = 66 ms), 118 commits in 10 s; GDK_DEBUG=frames: paint 2.6 ms, "sleep" 66-75 ms between frames. WebKit's own getVideoPlaybackQuality() at the same time: 60-61 frames/s presented to its compositor, 1 drop in 45 s.
first-learned: 2026-09-02
---

`tools/tk-fps.py` and `tools/tk-gesture-bench.py` read the DPU's frame
counter. That is the number of frames **phoc** sent to the panel. phoc
repaints every vsync while any client is animating (and the video subsurface
keeps it animating), so the counter sits at 60 whatever the client does. A
client that updates its window every fourth vsync shows the same 60 as one
that updates every vsync -- the panel is simply shown the same client buffer
four times, which is exactly the "frames repainting" the user reported.

Three different frame rates exist in a browser, and only one is on the panel:

| stage | how to read it | YouTube on 2026-09-02 |
|---|---|---|
| media pipeline -> WebKit compositor | `tk-webvq.py` (getVideoPlaybackQuality via the inspector) | 60/s, ~0 dropped |
| WebKit compositor -> its window (the app's real frame rate) | `WAYLAND_DEBUG=1` commit intervals, or `wp_presentation_feedback.presented` sequence deltas | 12-15 fps idle video, ~30 fps drag |
| phoc -> panel | tk-fps.py / tk-gesture-bench | 60 |

So: **the DPU number is the control, never the verdict.** For an app, count
its own commits. `WAYLAND_DEBUG=1 app 2>log`, then intervals between
`-> wl_surface#N.commit()` on the busiest surface; `presented(...)` args carry
the vsync sequence, whose deltas are exact (4 = every fourth frame).
[[youtube-judder-is-2160p60-plus-a-lockstep-decoder-not-venus-throughput]]
was written against the DPU column and its "panel 60 vsync/s during every
stutter" line is true and irrelevant; the earlier handoffs' "0 jank" browser
scroll and "60 vsync/s fullscreen VP9 in Epiphany" figures were phoc's.
porthole-dev/porthole#43 asks the bench to grow a client-side column.
