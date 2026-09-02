---
id: wayland-debug-is-not-a-free-instrument
title: WAYLAND_DEBUG=1 grows a multi-hundred-MB tmpfs log inside the process you are measuring
scope: generic
subsystem: display
severity: trap
confidence: proven
evidence: "taimen 2026-09-02. An Epiphany launched with WAYLAND_DEBUG=1 for browser frame arms had written 127,844,556 bytes to /tmp/wl.log, on a phone whose /tmp is tmpfs (RAM) and whose WebProcess RSS was already ~1 GB of 3.6 GB. Growth ~1 MB/s during playback. Every browser frame number taken on this device across three sessions was collected with this enabled."
first-learned: 2026-09-02
---

**`WAYLAND_DEBUG=1` is the obvious way to get a client's own commit cadence,
and it is not free.** It formats and writes every protocol message from inside
the client. On this device that was **~1 MB/s**, and the log reached **127 MB**
in one session.

Two costs, and the second is the one that bites:

1. CPU in the client's own threads, formatting text on the path you are timing.
2. **`/tmp` is tmpfs.** The log is not on disk, it is in RAM. 127 MB of a
   3.6 GB phone, taken from a browser that already holds ~1 GB, and never
   freed until something deletes the file.

So the instrument competes for exactly the resource whose exhaustion produces
the stalls you are trying to explain. It is not a neutral observer.

**What to do instead.** For a page's frame cadence, ask the page --
`requestAnimationFrame` deltas collected in the page and read out through the
remote inspector cost nothing and need no privileges. Pair it with a
`setTimeout` chain and you also learn whether a stall is the compositor or the
main thread ([[the-browser-stutter-is-a-blocked-webkit-main-thread]]).

If you do need the protocol log, cap it (`| head -c`), point it somewhere that
is not RAM, and delete it between arms -- and do not compare an arm that had it
against one that did not.
