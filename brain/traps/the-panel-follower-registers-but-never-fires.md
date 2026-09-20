---
id: the-panel-follower-registers-but-never-fires
title: drm_panel_add_follower() succeeding does not mean the callbacks will ever run
scope: device:google-taimen
subsystem: input
severity: trap
confidence: proven
evidence: taimen 7.2.2 r79, 2026-09-20. ftm4 registers as a drm_panel follower: the DT node 0-0049 carries the panel phandle (visible in /proc/device-tree), drm_is_panel_follower() is true, devm_drm_panel_add_follower() returns 0, and the driver logs 'ftm4 0-0049: following the panel for screen-off handover'. With the userspace helper stopped, blanking the screen left dpms=Off and ftm4.handover=N, and an instrumented panel_disabling()/panel_enabled() logged nothing across a blank and an unblank. drivers/gpu/drm/msm makes no drm_panel_prepare/unprepare/enable/disable calls at all -- grep finds none -- so whichever bridge path a DPMS-off takes on this display does not reach drm_panel_disable(), which is what walks panel->followers
first-learned: 2026-09-20
---

**Symptom** — a driver follows the panel so it can act when the screen blanks.
Registration succeeds, the log line proves it, and the callbacks never run:

```
ftm4 0-0049: following the panel for screen-off handover
```

then `dpms=Off` with nothing done, and an instrumented `panel_disabling()` /
`panel_enabled()` printing nothing across a full blank and unblank.

**Why registration proves so little** — `drm_is_panel_follower()` is a test for
one devicetree property and nothing more; the helper says so itself:

```c
	/*
	 * The "panel" property is actually a phandle, but for simplicity we
	 * don't bother trying to parse it here. We just need to know if the
	 * property is there.
	 */
	return device_property_present(dev, "panel");
```

`drm_panel_add_follower()` then resolves the phandle, takes the panel and adds
you to `panel->followers` -- all of which can succeed while nothing ever walks
that list. The list is walked only from `drm_panel_prepare()`,
`drm_panel_unprepare()`, `drm_panel_enable()` and `drm_panel_disable()`. If the
display path never calls those, a follower is inert and silent.

**On this device** `drivers/gpu/drm/msm` calls **none** of them:

```sh
grep -rn 'drm_panel_prepare\|drm_panel_unprepare\|drm_panel_enable\|drm_panel_disable' drivers/gpu/drm/msm/
# no matches
```

`drm_panel_bridge` in `drivers/gpu/drm/bridge/panel.c` does call
`drm_panel_enable()`/`drm_panel_disable()`, so the question is whether the panel
is reached through that bridge on a DPMS-off, and on msm that is not settled.

**What to check before trusting a follower** — the log line is not the test.
Instrument the callback itself, blank the screen with nothing else armed, and
confirm it fired. That is a two-minute check and it is the difference between a
feature and a patch that only looks applied.

**Where this left things** — the screen-off handover is done from userspace
instead ([[the-slpi-subscription-must-come-after-the-handover]] explains why the
ordering matters), which works and is what ships. The follower remains
registered and unused.
