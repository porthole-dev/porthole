---
id: a-build-outlives-the-porthole-run-that-tracks-it
title: A build survives the porthole run that started it, and takes the buildroot lock to the grave
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "2026-09-01: `porthole aports build webkit2gtk-6.0 --yes` published .run/pkg-status.json normally (state running, pid 1562211, 7907/9429, 83.9%). Its own process was later gone -- `ps -p 1562211` empty -- while the build it started, host pid 1562417, was still compiling under conmon 1562414 with no porthole ancestor. The status file froze at that snapshot for 48 minutes while the build advanced to 8117/9429; `porthole pkg watch` read the frozen file, found the publisher's pid dead, and reported the previous run as finished 24m ago with exit 0. The buildroot flock had been released by the kernel when its holder died, so the mutex read as free with a build running in it."
first-learned: 2026-09-01
---

**`podman exec` runs the build on the server side. Kill the client -- an agent's
command timeout, a closed session, a Ctrl-C -- and the build does not die with
it.** What dies is everything porthole was doing on its behalf: the tracker that
publishes the status file, and the flock that keeps a second build out of the
buildroot.

Three consequences, in the order they bite:

1. **The status file freezes at a plausible number.** It still says
   `state: running`, so nothing in it looks wrong. It is simply the last thing
   the dead process managed to write.
2. **`pid_alive` asks about the wrong process.** The pid in the snapshot is the
   host-side tracker, not the build. Dead tracker plus live build reads as
   `stale`, and a watcher that trusts only the file concludes nothing is
   happening -- while the machine is at 86% of a nine-thousand-object build.
3. **The buildroot mutex is gone.** `flock` is released by the kernel when its
   holder dies, which is exactly the property you want when a build crashes and
   exactly the wrong one here: the lock is free, the buildroot is not, and the
   next build deletes this one's source tree
   ([[two-pmbootstrap-builds-destroy-each-other]]).

**How to tell, in two commands.** The status file's own pid, and the workspace's
process list -- the second is the only one that knows the truth:

    ps -p "$(jq -r .pid .run/pkg-status.json)"      # empty: the tracker is gone
    podman exec porthole-sandbox ps -eo pid=,args= | grep pmbootstrap

A pmbootstrap on the second list with nothing on the first is this trap. The
build is healthy; only the instrument died. `porthole pkg watch` now runs both
checks itself and says so, rather than reporting the last finished run:

    webkit2gtk-6.0 is still building, but nothing is following it any more --
    the porthole run that published pkg-status.json is gone, and its last
    numbers are 48m22s old

**What to do.** Let it finish -- the build is fine and its own output is the
live witness (`podman exec porthole-sandbox tail -f /pmb/log.txt`, whose
`[n/N]` lines are the real fraction). Do not start anything else in that
workspace until it ends, because nothing is holding the door any more. And
prefer `porthole pkg build --detach`, whose tracker is a session of its own and
does not die with the caller.
