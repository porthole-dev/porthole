---
id: the-30fps-lock-is-not-power-the-panel-runs-at-60
title: There may be no 30 fps lock at all -- the panel runs at 60 and ph-framprobe reports ~31 whether the screen is on or OFF
scope: device:google-taimen
subsystem: display
severity: finding
confidence: proven
evidence: the probe reports ~31 fps with the panel ON (vsync +302/+349/+347 per 5 s) and ~31 fps with the panel OFF (vsync delta 0, display IRQs frozen) -- the same number across opposite display states
refutes: the 30 fps session lock is caused by the suspend-era power policies, thermal throttling, the a540 AGC limiter, mesa or the GTK renderer; and that ph-framprobe.py's number describes the session at all on this device
first-learned: 2026-09-20
---

# The panel is at 60, the operator sees smooth scrolling, and the probe says 31 either way

**The measurement.** `ph-framprobe.py` against
`/sys/kernel/debug/dri/c901000.display-controller/encoder-0/status`, with the
panel verified on before AND after each run (`bl_power=0`,
`card0-DSI-1/enabled=enabled`) and idle blanking disabled:

    run1  31.4 fps   vsync +302 / 5 s  = 60.4 vsync/s
    run2  32.1 fps   vsync +349 / 5 s  = 69.8 vsync/s
    run3  31.6 fps   vsync +347 / 5 s  = 69.4 vsync/s

That reads like two frames of display for every one the client is given. **It
is not safe to conclude that**, and the operator caught why: phosh scrolls
smoothly, which a genuine half-rate session does not.

**THE PROBE REPORTS THE SAME ~31 fps WITH THE PANEL SWITCHED OFF.** Four runs
taken against a blanked screen gave 28.3/31.5/30.8/31.9 while `vsync` did not
move at all and both display IRQs (`msm`, `dsi_isr`) were frozen. A number
that does not change when the display stops existing is not measuring the
display. So ~31 is what this probe produces on this device, and the session's
real rate is not known from it.

What IS established: the panel delivers ~60 vsync/s under load, and the
operator reports smooth scrolling. There may be no session-wide half-rate
lock to explain.

**What this rules out, each measured on the same boot:**

- **Power policy.** The whole suspend-era set is armed -- TZ LMH, the a540
  on-die limiter, the vendor energy model, schedutil at `rate_limit_us=2000`.
  The die was 39 C and the GPU sat at its lowest OPP (257 MHz of 710
  available) because the load is trivial. Nothing was throttling anything.
- **mesa.** Measured across the fork rebase, 26.2.3-r0 stock and 26.2.3-r52
  with all four a5xx patches. Same rate.
- **The GTK renderer.** `gl` and `ngl`, interleaved, twice, on both mesa
  builds. 31.0/31.6 against 31.9/31.8. No difference outside noise.
- **The two things that fixed this once.** `0204-drm-msm-dpu-cmd-mode-send-the-pageflip-event-at-rd_p.patch`
  is in the shipping series and in the tree, and `FD_MESA_DEBUG=sysmem` is
  not set anywhere in the session. Those are the pair that
  [[the-session-is-back-to-30fps-on-7-2-and-ctl-start-is-not-why]] measured
  at 57.7 fps. Both present, and the rate is back at half.

**THE TRAP THAT COST THIS SESSION MOST OF AN HOUR, and it is not new.**
A blanked panel still delivers frame callbacks. On a screen that is OFF the
probe reports **~31 fps** -- indistinguishable from the real half-rate number
-- while `vsync` and both display IRQs (`msm`, `dsi_isr` in
`/proc/interrupts`) do not move at all. Four figures were taken that way after
a reboot and had to be retracted. With the panel genuinely mid-wake the same
probe reports 2.5-2.8 fps, which is also not a real number.

So: **no frame-rate figure on this device means anything unless the panel
state is read from the kernel either side of the run, and the DPU's vsync
counter is sampled across it.** `bl_power`/`enabled` for the first,
`encoder-0/status` for the second. This is the same law as
[[taimen-touch-test-discipline]], one subsystem over.

Idle blanking has to be disabled for the duration or it lands mid-run:
`gsettings set org.gnome.desktop.session idle-delay 0`.

**Where to look next.** Not at the display. Fix the INSTRUMENT first: find
why `ph-framprobe.py` self-limits to ~31 fps -- an unpresented or unfocused
surface that phoc throttles, or cost inside its own tick callback -- and give
it a positive control that fails when the window is not being presented. Until
it has one, no fps figure from it should be quoted, including the historical
57.7.

**Two tool bugs found on the way, both live:**

- `ph-session.sh wake` presses power twice without re-reading the panel
  between presses, so on a phone that woke on the first press it blanks it
  again and then reports `panel still off after two power presses`. A single
  `ph-key.py power` works.
- Several tools (`ph-session.sh`, `ph-chromium-videoarm.sh`, `ph-webarm.sh`,
  `ph-videoarm.sh`, `ph-scrollarm.sh`) require `/tmp/sess.sh` exporting the
  session environment, and nothing creates it. A reboot clears `/tmp`, after
  which every one of them fails with `can't open /tmp/sess.sh`. It can be
  rebuilt from the compositor's own environ:

      P=$(pgrep -x phosh); sudo tr '\0' '\n' < /proc/$P/environ |
        grep -E '^(WAYLAND_DISPLAY|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|XDG_CURRENT_DESKTOP)=' |
        sed 's/^/export /' > /tmp/sess.sh
