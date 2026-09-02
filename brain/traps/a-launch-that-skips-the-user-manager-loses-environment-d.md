---
id: a-launch-that-skips-the-user-manager-loses-environment-d
title: A browser launched from ssh, systemd-run --scope, or set-environment does not get environment.d -- and on taimen that means Skia-GPU
scope: generic
subsystem: graphics
severity: trap
confidence: proven
evidence: 2026-09-02 taimen, webkit2gtk-6.0 2.52.6. Epiphany launched via `systemd-run --user --scope ... epiphany` from an ssh shell: /proc/<WebKitWebProcess>/environ had no WEBKIT_SKIA_ENABLE_CPU_RENDERING; YouTube's like/dislike/bookmark/menu icons rendered as noise, the tab's WebKitWebProcess held 877-912 MB of DRM memory (fdinfo), the scope hit MemoryHigh=1500M in 20 s and systemd-oomd killed it at +87 s (app.slice pressure 80.85% > 50% for 30 s). Same URL with the env passed explicitly on the command line: environ shows the flag, every icon clean, DRM 339 MB, scope 898 MB, swap 0, no oomd. `systemctl --user set-environment WEBKIT_SKIA_USE_LINEAR_TILE_TEXTURES=1` before a --scope launch: web process dmabuf_fds=0 (flag never arrived); same flag on the command line: 167 dma-buf fds and 178 PRIME_HANDLE_TO_FD in strace.
first-learned: 2026-09-02
---

**`~/.config/environment.d/*.conf` reaches only what the user manager spawns.**
An icon tap (D-Bus activation), `gtk-launch` through the session bus, and
`systemd-run --user` *services* get it. These do not:

- `ssh phone 'setsid epiphany URL'` -- the ssh session's environment;
- `systemd-run --user --scope ... epiphany` -- a scope is your own fork,
  moved into a cgroup; it inherits **your** environment, not the manager's;
- `systemctl --user set-environment FOO=1` followed by either of the above --
  it changes the manager's block, which those launches never read.

The launch *looks* right (real `app-*.scope`, powerhintd fires, mempressure
finds it) and the browser comes up, so nothing complains. On taimen the
missing variable is `WEBKIT_SKIA_ENABLE_CPU_RENDERING=1`, and without it
WebKit paints with Skia-GPU on the a540 -- the path
[[panel-corruption-was-gpu-reset-wreckage-not-tearing]] condemned. Three
symptoms follow and each was chased as its own bug before the environ check:

| symptom | Skia-GPU (flag lost) | CPU rendering (flag present) |
|---|---|---|
| YouTube icons | like/dislike/bookmark/menu filled with noise | clean |
| tab WebProcess DRM memory | 877-912 MB | 339 MB |
| scope after 35 s, page idle | 1211-1311 MB, swapping, oomd at +87 s | 898 MB, swap 0, no oomd |

**Positive control, every arm**: `sudo tr '\0' '\n' < /proc/$(pgrep
WebKitWebProc | tail -1)/environ | grep WEBKIT_`. An arm whose variable is not
in that output measured the default, whatever the launcher said. For flags
that change allocation, add a second control that cannot lie:
`WEBKIT_SKIA_USE_LINEAR_TILE_TEXTURES=1` must show dma-buf fds in the web
process (`ls -l /proc/PID/fd | grep -c dmabuf`); zero means it fell back.

**How to launch for a measurement**: pass the variables on the command line
of the scope, e.g. `systemd-run --user --scope -u app-gnome-org.gnome.Epiphany-$$.scope env WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 ... epiphany URL`,
and read them back from environ. Also delete
`~/.local/share/epiphany/session_state.xml` between arms: every `epiphany URL`
against a running instance adds a tab, an oomd kill keeps them all, and the
next launch restores every one -- sixteen YouTube tabs and eight web processes
were "the memory ceiling" for three arms on 2026-09-02.
