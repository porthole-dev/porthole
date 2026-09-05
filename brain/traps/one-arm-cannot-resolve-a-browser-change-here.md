---
id: one-arm-cannot-resolve-a-browser-change-here
title: "One browser arm cannot resolve anything under ~10% here: the same build gave 29% and 73% frames-over-budget"
scope: device:google-taimen
subsystem: graphics
severity: trap
confidence: proven
evidence: five arms of webkit 2.52.6-r55 on the same page, 2026-09-05 -- playback p50 31.5/33.4/33.8/34.0/34.7 ms and frames-over-33ms 29/54/58/62/73 percent; three r53 arms by contrast gave p50 34.2/34.3/34.4 and 65/68/69 percent
first-learned: 2026-09-05
---

**Do not** compare browser builds with one arm each. On m.youtube.com the
frames-over-budget metric swings **29% to 73% on the same binary**. A single
pair of arms will therefore "show" almost any result you like, in either
direction, and it is very easy to write the flattering one down.

This was caught the honest way and only just: an r55 arm gave 29% against a
baseline's 68%, which was reported as "frames over budget more than halved".
A repeat of the same build gave 54%, then 58, 62 and 73. The median was 58%
and the real effect was about 1.5% on p50 -- inside the noise.

**Instead**: at least three arms per build, compare medians, and quote the
range. Budget ~7 minutes per arm.

Two things that make it worse than ordinary noise:

- **The spread is not the same for every build.** r53 is tight (p50
  34.2-34.4, 65-69%) while r55 is wide (31.5-34.7, 29-73%). A patch whose
  benefit depends on the page -- damage-driven culling, for instance -- adds
  variance rather than shifting the mean, so "high variance" is itself a
  signal that the code is doing something, and comparing single arms hides
  exactly that.
- **Back-to-back arms heat up.** Even cooling the die to 48 C between runs,
  the third arm in a row reached 79 C where the first reached 74 C: the
  chassis and battery stay warm and the die climbs faster. Cool to a tight
  floor ([[taimen-thermal-hygiene]] / `tools/tk-thermal.sh cool`) and expect
  later arms in a series to be slower.

And the standing companion to this: a metric moving is not evidence the code
moved it. Instrument the patch to prove it executed -- a skip counter, a log
line -- because "the change had no effect" and "the change never ran" are
indistinguishable from the outside and want opposite responses.
