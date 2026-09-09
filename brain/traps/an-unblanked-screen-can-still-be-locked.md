---
id: an-unblanked-screen-can-still-be-locked
title: Turning the output back on does not unlock it: every injected gesture then drives the lockscreen and the app under it never moves
scope: generic
subsystem: display
severity: trap
confidence: proven
evidence: taimen 2026-09-09. Three scroll arms in a row reported 'NOT MEASURED: 102 frames in 6.2s. The screen never changed' and scrollY 0 -> 0 with a healthy Epiphany showing the article -- grim showed phosh's lockscreen ('Slide up to unlock') on top. org.gnome.ScreenSaver.SetActive(false) had returned success and the output WAS on. 'sudo loginctl unlock-sessions' dismissed it; the same arm then scrolled 0 -> 17614 and reported 638 client commits. org.gnome.desktop.session idle-delay is 60 s on this image, so any arm whose page load takes a minute starts locked.
first-learned: 2026-09-09
---

**Symptom** — a gesture arm that runs, produces a full 60 Hz of frames, and
moves the page zero pixels:

    drag         on org.gnome.Epiphany
      NOT MEASURED: 102 frames in 6.2s. The screen never changed during the
      run -- the gesture hit nothing that animates.
    [arm] scrollY 0 -> 0

The frames are real. They are **phosh's**, animating its own lockscreen while
the injected drag swipes at "Slide up to unlock". The browser is up, mapped,
focused and one surface underneath, and it commits nothing all run.

**Why unblank is not enough** — [[an-injected-touch-does-not-wake-a-blanked-screen]]
covers the output being *off*; `org.gnome.ScreenSaver.SetActive(false)` fixes
that, returns success, and leaves the session **locked**. The screensaver
interface has no Unlock. The lever is logind:

    sudo -n loginctl unlock-sessions

`ph-ui.py unblank` now does both and reports failure if either half refused, so
anything routed through `ph-gesture-bench.py` is covered.

**Why it bites unattended arms particularly** — `org.gnome.desktop.session
idle-delay` is 60 s on this image. Any arm that waits for a page to load, or
for a build, or for a video to buffer, spends longer than that with no input
and starts its measurement locked. It does not matter that the arm unblanked
at the top: the lock arrives during the wait.

**The instrument that finds it in one shot** — `grim -t png /tmp/pre.png`
immediately before the gesture, and look at it. Three arms were spent reasoning
about touch injection, focus and coordinates for a symptom a single screenshot
named instantly. A gesture arm should capture the frame it is about to drive.
