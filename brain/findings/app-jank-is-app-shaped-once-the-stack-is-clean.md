---
id: app-jank-is-app-shaped-once-the-stack-is-clean
title: With the display/decode stack clean, the remaining jank is app-shaped -- GJS GC in Maps, main-thread layout in WebKit 2.48, init CPU in browser launches
scope: device:google-taimen
subsystem: display
severity: finding
confidence: proven
evidence: 2026-09-01 late night, kernel #26 (packaged 0202-0205), tk-gesture-bench + tk-webbench + cold/warm launch timing on the phone
refutes: the residual jank needs more kernel or vendor tuning; readahead fixes browser cold starts; Maps jank is tile network fetch; Firefox scrolls badly on this stack
first-learned: 2026-09-01
---

**The question** — with 60 fps compositing, hardware decode, powerhintd and
the bus votes all landed, apps still jank in places. Which layer owns what
remains?

**The apps do.** Measured on the packaged #26 kernel, same instruments:

- **phosh session**: fling p50 17.0 / p90 18.1 — the floor is clean.
- **Firefox ESR 140, heavy feed**: drag PERFECT (max 19.5 ms, 0 jank) —
  async pan/zoom keeps scrolling off the main thread. Fling: one 42 ms
  single. This is the architecture WebKitGTK grew in 2.50-2.53 and what the
  2.52.6 fork build exists to import.
- **Epiphany/WebKit 2.48**: drag stalls ~116 ms = main-thread layout of
  newly revealed content. Known upstream, fixed in the 2.50-2.53 cycle.
- **gnome-maps 50.4**: the worst measured -- drag 10 janks >33 ms, max
  200 ms; warm tiles barely help (8 janks), and the thread running during
  every stall is the GJS main thread. SpiderMonkey GC + JS tile placement in
  libshumate. No kernel knob reaches an app's GC pause; upstream gnome-maps.
- **Launches**: small GTK apps are 50-90 ms warm / ~1 s true-cold. Epiphany
  is 3.3 s cold, 2.2 s warm-to-window -- and a full library prewarm (680 ms
  of reads) recovers only ~0.7 s, so ~2.2 s is CPU-bound init (linking
  ~200 MB across ~5 processes, GObject registration, WebKit setup).
  read_ahead_kb 128->2048: NO effect on cold launch -- refuted. A login
  cache warmer would save ~1 s once per boot; the real cost is WebKit
  startup, whose Android answer is zygote-style preloading (keep it
  resident).

**Instrument notes** — launching via ssh `setsid app` creates no app-*.scope
cgroup, so powerhintd's LAUNCH hint does NOT fire for benchmarked launches;
real icon taps are faster than these numbers. Measure launch to
window-visible (lswt), not to dbus name -- Epiphany's dbus name appears at
~0.4 s while the window takes 2 s more. And wait for the OLD window to
disappear before timing, or the measurement is of nothing. busybox date has
no %N -- use /proc/uptime (this trap is now three sessions old).
