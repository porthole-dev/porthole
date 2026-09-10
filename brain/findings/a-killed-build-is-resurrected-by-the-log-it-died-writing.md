---
id: a-killed-build-is-resurrected-by-the-log-it-died-writing
title: A killed build is resurrected by the shared log it died writing
scope: generic
subsystem: statusline
severity: finding
confidence: proven
evidence: 2026-09-10, reproduced as tests/test_statusline.py::test_a_cancelled_build_is_not_resurrected_from_the_shared_log
refutes: the status file is stale after pkg stop; pkg stop failed to kill the build; the status line caches its last render across a reattach; a phantom row means something is still running inside the container
first-learned: 2026-09-10
---

**The question** — `porthole pkg stop` returned `stopped pid 22437`, and the
status line went on showing the build for about two minutes:

```
device-google-taimen [▎─────────────────] 1% quiet 1m41s 87h54m build 1008/55856 ·
```

Nothing was running. Why does a cancelled build keep a row, and where does an
87-hour ETA come from?

**The answer** — `build_snapshot`'s last resort synthesises a row from the
shared pmbootstrap log, for builds that publish no status file at all
(`sandbox shell --command`, or one started after the last tracked build wrote
`done`). It has three guards against inventing a build, and a kill defeats all
three at once:

| guard | what a kill does to it |
|---|---|
| `log_outcome()` — did the log say how this build ended? | abuild was killed mid-step. No outcome line was ever written. |
| `log_invocation_ended()` — did pmbootstrap print `DONE!`? | `_kill_inside()` is `pkill -f 'pmbootstrap.*build'`. A pkilled process prints nothing. |
| `names_a_build()` + fresh mtime — is a build writing this log now? | The tail is genuine ninja steps, and the mtime is fresh **because** the build was killed a moment ago. |

So the row survives for `LOG_FRESH_S` (120 s) and then vanishes, which is the
"phantom for a little bit". The ETA is extrapolated from
`statusline-samples.json`, whose samples stopped advancing at the kill — 87h54m
is what 1008 of 55856 steps looks like at a rate that has gone to nearly zero.

The part worth keeping: **this checkout had already published the right answer
and it was not consulted.** `_stop` writes `state: failed` into
`pkg-status.json` before returning, and `build_snapshot` reads that file only
on the paths where it says *running*. A verdict this checkout wrote is better
evidence than a log every pmbootstrap invocation in the workspace shares.

**What this rules out** — the status file is NOT stale; `_stop` updates it
correctly and `pkg status --json` says `failed` throughout. `pkg stop` did NOT
fail to kill anything; both sides of the container boundary are dead. The
status line does NOT cache a render or replay one across a reattach — every
refresh is a fresh process that recomputes this from files. And the row is not
evidence that something survived inside the container; do not go hunting for a
process to kill.

**How it was established** — the three guards were read in
`porthole_progress.live_build_from_log`, then reproduced without a container:
a staged `APKBUILD` naming the package, a log tail of ninja steps with no
`DONE!` and an mtime 101 s old, and a `pkg-status.json` saying `failed`. The
synthesised snapshot came back `state: running`, `1008/55856`, `last_age:
101.0` — the reported row, digit for digit.

The fix keys on the two things that separate a corpse from an untracked build:
the published verdict must name the **same** package, and the log must have
been **quiet since** that verdict. A log that moved on is a new build starting,
not the old one's remains.

This is the same shape as
[[a-freshness-indicator-must-measure-the-thing-you-actually-read]], in a second
place: the mtime being measured is not the thing whose liveness is claimed.
