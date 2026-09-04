---
id: phosh-aborts-on-a-gpu-reset-and-takes-the-session-with-it
title: A GPU reset aborts phosh, not phoc -- the session restarts and looks like a reboot
scope: device:google-taimen
subsystem: graphics
severity: finding
confidence: proven
evidence: two a540 faults on 2026-09-04 (23:23:24 and 23:57), each followed within seconds by a phosh SIGABRT coredump; phoc survived both, its PID changed only because greetd restarted the session; uptime unchanged across both
refutes: the compositor dies on a GPU reset; a session restart means the phone rebooted; pkill -x phosh is a fix rather than a symptom
first-learned: 2026-09-04
---

**The question** — after an a540 fault the screen goes away and comes back,
the phoc PID has changed, and it looks for all the world like the phone
rebooted. It has not: `uptime` runs straight through. So what actually died?

**The answer** — **phosh**, with SIGABRT, within a minute of each GPU reset:

    23:23:24  phoc: Re-creating renderer after GPU reset
    23:24:09  SIGABRT  /usr/libexec/phosh
    23:57:05  phoc: Re-creating renderer after GPU reset
    23:57:30  SIGABRT  /usr/libexec/phosh

phoc does **not** crash. It handles the renderer loss correctly -- it recreates
the renderer, and with the wlroots re-import patch it also keeps every client
texture ([[wlroots-never-re-imports-a-texture-after-a-renderer-swap]]). phosh
is a separate GTK process with its own GL context, and that context is lost
too. It aborts; `mobi.phosh.Shell.service` going down takes the phrog/greetd
session with it, which restarts phoc -- hence the new phoc PID that makes this
look like a reboot.

The abort's stack is unsymbolised without `phosh-dbg`, but carries
`cairo_surface_reference` and **libepoxy** -- the GL dispatch library -- which
is consistent with a lost GL context rather than anything phoc did.

**What this rules out** —

- **"The compositor died."** It did not. phoc survived both events; only its
  PID changed, and only because the session around it restarted.
- **"The phone rebooted."** `uptime` is continuous across both. Check uptime
  before believing a reboot, especially since
  [[androidboot-bootreason-always-says-watchdog-here]] will not tell you.
- **"`pkill -x phosh; systemctl --user restart mobi.phosh.Shell.target` is the
  fix."** That was the documented recovery, and it is a symptom treatment: the
  session is already gone by then. The bug is that phosh cannot survive losing
  its GL context.

**How it was established** — `coredumpctl list` after two resets that were
induced accidentally, by launching Epiphany through `systemd-run` from ssh,
which loses `~/.config/environment.d` and therefore
`WEBKIT_SKIA_ENABLE_CPU_RENDERING=1`, putting Skia back on the GPU
([[a-launch-that-skips-the-user-manager-loses-environment-d]]). That is a
reliable way to reproduce a reset if one is needed -- and a reliable way to
wreck a measurement if it is not.

The fix to look for is in phosh: handle `GL_..._CONTEXT_RESET` /
`EGL_CONTEXT_LOST` by rebuilding its GL resources instead of aborting. Install
`phosh-dbg` first and re-take the trace; the frames above are addresses only.
