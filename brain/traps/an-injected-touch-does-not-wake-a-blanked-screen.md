---
id: an-injected-touch-does-not-wake-a-blanked-screen
title: An injected touch does not wake a blanked screen, and every gesture arm then measures a still image
scope: generic
subsystem: display
severity: trap
confidence: proven
evidence: taimen 2026-09-05 -- with the panel blanked, `ph-gesture-bench.py drag 720 2400 720 900 500 3` moved `window.scrollY` 0 -> 0 and counted 0 frames in 10.2 s; the identical drag after `org.gnome.ScreenSaver.SetActive false` moved it 0 -> 1721. Writing `bl_power=0` as root lit the backlight and the same drag still moved 0 px
first-learned: 2026-09-05
---

**Symptom** — a gesture arm that ran, printed no error, and measured nothing:

    drag         on org.gnome.Epiphany
      NOT MEASURED: 0 frames in 10.2s. The screen never changed during the
      run -- the gesture hit nothing that animates.
    [rec] scrollY 0 -> 0

Or, worse, an arm whose instrument does not check: uprobes armed on the render
phases report **zero hits for every phase**, which reads exactly like "this
workload does not use the rendering pipeline". A whole session's conclusion was
built on that null, and it was wrong -- see
[[the-scroll-stall-is-the-pages-own-javascript]].

**Do not** assume a phone that answers ssh has a screen that is on. phosh's
screensaver powers the output down after a few minutes idle, and **uinput
events do not undo it**: the injected touch reaches the client, but never the
idle notifier, so the compositor keeps the output off. An unattended arm --
which is every arm, since it takes minutes and nobody is watching the panel --
starts blanked by construction.

Nothing in the numbers says so. The DPU counter reads 0 frames, which also
happens for a gesture aimed at an end stop; `grim` blocks and times out, which
also happens on a wedged compositor; and the phone is otherwise perfectly
healthy.

**The backlight is not the lever.** `echo 0 > /sys/class/backlight/*/bl_power`
as root turns the backlight on and changes nothing else: the compositor's output
stays off and the same drag still moves the page zero pixels. Measured, not
assumed.

**The fix** is the screensaver's own call, and it has to run **as the session
user** -- from root the session bus answers `Call failed: Socket not connected`:

    busctl --user call org.gnome.ScreenSaver /org/gnome/ScreenSaver \
        org.gnome.ScreenSaver SetActive b false

`tools/ph-ui.py unblank` is that call, dropping back to the session user with
`sudo -u` when it is invoked as root, and `ph-gesture-bench.py` now runs it
before every measurement -- so every arm in this tree that drives a gesture is
covered without changing the arm. If you are driving the screen some other way,
call it yourself before you believe a frame count.

**Related** — [[the-instrument-is-guilty-until-proven-innocent]],
[[uprobes-do-not-attach-to-an-already-mapped-library]].
