---
id: a-new-window-lands-in-recents-when-the-phosh-grid-is-open
title: With the phosh app grid open, a launched app goes straight to recents -- and every measurement of it is void
scope: generic
subsystem: display
severity: trap
confidence: proven
evidence: "taimen 2026-09-02. With the overview open, three consecutive fresh Epiphany launches (systemd-run --user --scope, new window, session_state.xml removed) never reached the panel; the grid stayed on screen with an unchanged carousel while the clock advanced. `wlrctl toplevel focus app_id:org.gnome.Epiphany` returned success and changed nothing. Tapping the window's card in the carousel raised it immediately."
first-learned: 2026-09-02
---

**If the phosh overview/app grid is open, launching an app does not bring it to
the front.** It is created, it is mapped, it renders -- into a card in the
recents carousel. The grid stays on top, because it is a **layer-shell**
surface and layer-shell sits above every xdg toplevel. Raising the toplevel
therefore cannot help:

```sh
wlrctl toplevel focus app_id:org.gnome.Epiphany   # succeeds, changes nothing
```

This is worse than a cosmetic annoyance, because the app **keeps compositing**
into its thumbnail. Every symptom of a healthy app is present:

- `lswt` lists the toplevel with the right title
- the page reports `document.visibilityState === "visible"`
- `WAYLAND_DEBUG` shows the client committing buffers steadily
- the inspector evaluates JavaScript normally, the video plays, `currentTime`
  advances

...and the numbers are still meaningless, because what you are measuring is a
scaled thumbnail behind an overlay, not the window on the panel.

**`document.hasFocus()` is not the check.** It reports *keyboard* focus. A
window that is fully on screen and being interacted with reports
`hasFocus() === false` routinely (measured here on a foreground Epiphany
showing a YouTube page). Using it as a visibility gate rejects good arms and,
inverted, accepts bad ones.

**The check that works is a screenshot.** `grim` costs a few hundred
milliseconds and is the only instrument here that answers "what is on the
panel". Take one *before* the measurement window, not after the numbers
disappoint:

```sh
grim -s 0.5 /tmp/shot.png     # then look at it
```

**To raise the window**, tap its card in the carousel -- that both closes the
grid and activates the toplevel. Panel coordinates, via `ph-touch.py tap X Y`;
swipe the carousel first if the card is not centred. Nothing programmatic was
found that beats the layer-shell overlay.

Related: [[the-dpu-counter-is-phocs-frame-rate-not-the-apps]] --
both are the same mistake, measuring something adjacent to the app and calling
it the app.
