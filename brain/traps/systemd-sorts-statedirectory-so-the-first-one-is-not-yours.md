---
id: systemd-sorts-statedirectory-so-the-first-one-is-not-yours
title: A drop-in that adds a StateDirectory= reorders $STATE_DIRECTORY, and the daemon writes to whichever sorts first
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-09-13, taimen, fprintd 1.94.5 (stock unit StateDirectory=fprint) with a drop-in adding StateDirectory=fpctee. The service saw STATE_DIRECTORY=/var/lib/fpctee:/var/lib/fprint -- alphabetical, not unit-then-drop-in order -- and fprintd, which takes the first entry, stored users' prints under /var/lib/fpctee. Replaced with ReadWritePaths=-/var/lib/fpctee plus a tmpfiles.d "d" line; STATE_DIRECTORY back to /var/lib/fprint.
first-learned: 2026-09-13
---

**Do not add a second `StateDirectory=` in a drop-in to give a sandboxed
service somewhere else to write.** systemd sorts the unit's directories
before exporting them, so `$STATE_DIRECTORY` does not follow the order they
were written in. Any daemon that reads the first entry of that list
(fprintd does, for its per-user prints) may start writing its own state
into the directory you added for something else. fprintd then reads every
entry there as a username.

Give the extra directory write access instead:
`ReadWritePaths=-/var/lib/<dir>` in the drop-in, with the directory created
by a `tmpfiles.d` `d` line. It needs to exist before the service starts,
because `ProtectSystem=strict` leaves `/var/lib` read-only inside.
