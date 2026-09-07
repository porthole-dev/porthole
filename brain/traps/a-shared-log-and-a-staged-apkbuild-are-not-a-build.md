---
id: a-shared-log-and-a-staged-apkbuild-are-not-a-build
title: A staged package name and a touched log invent a build that is not running
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "2026-09-07: with nothing building, `porthole statusline` and `porthole pkg status` both reported webkit2gtk-6.0 running at 2h30m elapsed. The name came from chroot_buildroot_aarch64/home/pmos/build/APKBUILD, staged 2026-09-06 15:08 by a build that had finished the day before; the liveness came from log.txt's mtime, 2026-09-07 01:31, written by an unrelated pmbootstrap; the elapsed was one subtracted from the other. The buildroot flock was free and no pmbootstrap was running. Reported once before, 2026-09-06, as `device-google-taimen [ unknown ] -- 42m50s build - reattached`, where the freshness came from a `ccache -s` five seconds earlier. Fixed in the status line, then again in pkg status, then a third time in the function they share."
first-learned: 2026-09-07
---

**Symptom** — a phantom build: a row for a package nobody started. It has a plausible
elapsed time, no progress and no pid, and it survives refresh after refresh:

    webkit2gtk-6.0  [ unknown ]  --  2h30m  build

or `porthole pkg status` describing a build "started outside `porthole pkg`"
that does not exist. `podman exec porthole-sandbox ps -eo args= | grep
pmbootstrap` finds nothing, and the buildroot lock is free.

## Three inputs, each individually honest

`porthole_progress.live_build_from_log` is the untracked-build fallback: it
answers for a build that published no status file and never will. It has two
facts, and on 2026-09-07 both were true and neither was about a build.

| fact | source | what it actually says |
|---|---|---|
| the name | `porthole_buildroot.staged_build_name`, from `chroot_buildroot_*/home/pmos/build/APKBUILD` | which package was **staged**. The directory outlives the build that wrote it -- by a day, in the reproduction. |
| liveness | the shared log's mtime, within `LOG_FRESH_S` (120 s) | some pmbootstrap wrote the log. The log is shared by the **whole workspace**. |
| "has it ended?" | `log_invocation_ended` -- last non-trailer line is `DONE!` | whether the invocation *writing the log* finished. An open `pmbootstrap chroot` has not. |

Nothing there asks whether the writer is a **build**. `pmbootstrap status`,
`pmbootstrap log`, a `chroot -- ccache -s`, and an agent sitting in
`porthole sandbox shell` with a chroot open all write `log.txt`, and none of
them is a build.

## The rule

**A display may claim a build is running only when the log tail carries a line
only a build writes.** `porthole_progress.names_a_build` is that rule -- ninja's
`[n/N]`, abuild's `>>> <pkg>:` banners, kbuild's `CC`/`LD` prefixes. Any line
in the tail, not the last.

`staged_build_name` names and never decides liveness; its own docstring says
so. The guard belongs in `live_build_from_log`, which both displays call --
this class has been patched separately in the status line and in `pkg status`
before, and the two diverged.

## It overshoots in both directions

That is why it keeps coming back, and why a fix needs both tests:

- **inventing a build** -- `test_an_open_chroot_session_does_not_become_a_running_build`
- **blanking a real one** -- `test_a_real_untracked_build_is_still_reported`,
  and `test_a_quiet_packaging_phase_is_still_a_build`, because abuild is the
  only thing talking during fakeroot/strip/compress and that is minutes on a
  package with many subpackages. A guard that demanded a *recent* ninja line
  would blank a healthy build exactly when somebody is watching hardest.

Declining costs the first seconds of a genuinely untracked build, before
ninja or abuild has said anything. That is the right way round: a row that
appears late is a delay, a row that names a build nobody started is a lie
somebody acts on.

## Not an agent bypassing the verbs

The theory to kill: "an agent ran `pmbootstrap` by hand instead of a porthole
verb, so enforce the verbs." Refusing a raw `pmbootstrap build` inside
`sandbox shell` would close one route in and leave the display just as
credulous about every other one -- an agent's own container, a second
checkout, a `podman exec` typed by hand. Every route produces the same three
inputs. The display is what has to be honest.

Related: [[a-build-outlives-the-porthole-run-that-tracks-it]] is the same
symptom from the opposite cause -- a real build whose tracker died -- and
[[timestamps-cannot-prove-a-build-is-fresh]] is the general form: metadata
around stale content.
