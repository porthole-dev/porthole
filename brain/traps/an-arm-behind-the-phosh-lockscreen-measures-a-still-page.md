---
id: an-arm-behind-the-phosh-lockscreen-measures-a-still-page
title: An arm behind the phosh lockscreen measures a still page, and it looks exactly like the change under test breaking WebKit
scope: device:google-taimen
subsystem: graphics
severity: trap
confidence: proven
evidence: three void browser arms 2026-09-04 after an unplanned reboot; requestAnimationFrame fired once in 3 s, movie_player.playVideo() and a click on the play button both did nothing, playerState stuck at -1
first-learned: 2026-09-04
---

**Do not** launch a browser arm without unlocking first. The phosh lockscreen
is a layer-shell surface drawn **over** a fullscreen app, so the app is up, is
scriptable through the remote inspector, and answers every question you ask
it -- while being invisible and therefore not rendering.

What that looks like from the outside is a change that broke the browser:

- `movie_player.playVideo()` returns and does nothing. Called five times, six
  seconds apart, it still does nothing.
- Clicking `.ytp-large-play-button` through the inspector does nothing.
- `videoWidth` stays 0, `readyState` 0, `playerState` -1, `paused` true.
- **`requestAnimationFrame` fires exactly once and never again.** That was
  read as "the feature flag under test kills WebKit's rendering update loop",
  which is a perfectly sensible conclusion from that number and completely
  wrong. A page that is not visible does not get a rendering update, so a
  media element that needs one never starts.

The tell is that the *page* is fine: `document.title` is right,
`playabilityStatus` is `OK`, `location.href` is the watch page. Only the
pixels are missing, and nothing in the DOM knows that.

**Instead**: take a screenshot and look at it before believing an arm, and
swipe before launching. The PIN is disabled on this device, so the swipe is
the whole unlock and is harmless when nothing was locked:

    sudo -n python3 /tmp/ph-touch.py swipe 720 2600 720 1000 350

Every "proper" API lies about this -- logind `LockedHint`,
`org.gnome.ScreenSaver.GetActive` and `lswt` all say unlocked while the
lockscreen is plainly on screen, and `SetActive false` returns success and
changes nothing. `tools/repro/a5xx-gmem/session_state.sh` template-matches the
"Slide up to unlock" banner for this reason.

The reason this bites after a quiet session is that a reboot brings the
session back **locked** and wipes `/tmp`, so the helper scripts go too: an arm
that ran fine an hour ago is not evidence that the next one will.
[[ram-does-not-survive-a-reset-here]] is how you find out you rebooted at all.
