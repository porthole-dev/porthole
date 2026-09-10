---
id: a-freshness-indicator-must-measure-the-thing-you-actually-read
title: A freshness indicator that measures a different object than the one you compared turns "nobody looked" into confidence
scope: generic
subsystem: tooling
severity: trap
confidence: proven
evidence: porthole pkg drift reported mesa SAFE against a 1009-commit-stale worktree while printing a timestamp taken from .git/FETCH_HEAD's mtime
first-learned: 2026-09-09
---

**Do not** derive a "last updated" line from one object and then compare a
different one. `porthole pkg drift` was written to catch a carried fork being
overtaken upstream. It read each upstream APKBUILD from the **checked-out
working tree** of `aports_upstream`, and printed its freshness line from the
**mtime of `.git/FETCH_HEAD`**. Those are two different objects, and a plain
`git fetch` moves only the second:

    worktree HEAD           2026-08-20   ## master...origin/master [behind 1009]
    .git/FETCH_HEAD mtime   2026-09-09   <- what the tool printed
    main/mesa (worktree)    pkgver=26.1.6
    main/mesa (origin/master) pkgver=26.2.2

So the tool printed

    upstream tree last fetched   2026-09-09
      mesa   26.1.6-r14   upstream 26.1.6-r0   SAFE

while mesa's three a5xx patches were in fact already outranked. The verdict was
not merely wrong; it was wrong **with a timestamp asserting it was current**,
which is worse than no tool at all. A missing check invites you to look. A
confident all-clear stops you looking.

**Why this is easy to get wrong.** `git fetch` feels like "update the tree",
and in a checkout nobody edits it is easy to believe the working files track
the remote. They do not: fetch updates remote-tracking refs and leaves the
worktree exactly where it was. Any tool that reads files from a fetched-but-not
-merged checkout is reading history.

**What to do instead.** Read the ref you are claiming freshness for, and date
the ref you actually read:

    git -C <repo> show <ref>:<path>            # not the worktree file
    git -C <repo> log -1 --format=%cs <ref>    # date of what you just read

Reading a ref is offline, so this costs nothing and adds no network side
effect. If you keep a worktree fallback for checkouts with no remote, make the
output say which source each row came from -- a silent fallback to the stale
thing is the original bug wearing a different hat. And say so when the worktree
itself is behind, because the reader will otherwise draw conclusions about a
checkout they think is current.

**The general shape, beyond git.** Whenever output pairs a value with a
statement about that value's age or provenance, check that both come from the
same object. A cache's timestamp beside a recomputed value, a package index's
date beside a version read from disk, a probe's "as of" beside a number from a
different read -- the same trap, and it always resolves the same way: date the
thing you read, or read the thing you dated.

Related: [[the-instrument-is-guilty-until-proven-innocent]],
[[an-instrument-that-fails-quietly-is-worse-than-none]].
