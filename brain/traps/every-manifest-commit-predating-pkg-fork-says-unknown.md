---
id: every-manifest-commit-predating-pkg-fork-says-unknown
title: Every manifest commit predating pkg fork says unknown
scope: device:google-taimen
subsystem: packaging
severity: trap
confidence: proven
evidence: 2026-09-10. profiles/google-taimen/aports.conf, all six entries: `commit: unknown (backfilled 2026-09-09)`. `pkg fork` has recorded a real sha since #84, but nothing existing was re-forked, so nothing has one. `pkg rebase` recovers mesa's base by walking `main/mesa/APKBUILD` in aports_upstream for pkgver=26.1.6 pkgrel=0 -> e744e23b7a63, two upstream bumps back. `git log -n 400` over one APKBUILD path covers roughly two years of Alpine history for a package bumped as often as mesa.
first-learned: 2026-09-10
---

**Anything that needs the point a fork was taken from cannot read `commit:`
out of the manifest. It has to recover it from `forked:`, and it has to
refuse when it cannot.**

`porthole pkg fork` writes `upstream:`, `forked:` and `commit:` at fork time,
which is the root-cause fix §8 of the fork-provenance design asks for. It
works. It just does not apply retroactively to a single fork that exists
today: all six google-taimen entries were **backfilled** by reading the tree,
and a backfill can see the version a fork sits at but not the upstream commit
it was cut from. So every one says `unknown`, and will keep saying it until
that package is re-forked, which is not a thing anybody does on purpose.

The recovery that works:

    git -C <aports_upstream> log --format=%H -n 400 -- <path>/APKBUILD
    # then, per sha, `git show <sha>:<path>/APKBUILD` and match
    # pkgver/pkgrel against the manifest's `forked:` value

For mesa's `forked: 26.1.6-r0` that lands on `e744e23b`, two bumps back from
`origin/master`. It is exact, not approximate: the pair pkgver+pkgrel changes
on every upstream commit that matters, so the first match IS the fork point.

**Refuse rather than fall back.** The tempting failure is to give up on the
base and do a two-way diff against current upstream instead. That does not
degrade gracefully -- it silently reclassifies every change *upstream* made
since the fork as part of *our* delta, and then replays them onto a tree that
already has them. The result looks like a successful rebase and is wrong in a
way no test of ours would catch. `pkg rebase` exits `EX_STATE` and names what
it tried.

The trap generalises past this repo: **a field that is populated going forward
is not a field you can read today**, and the gap is exactly as long as the
lifetime of the objects created before it. See
[[a-freshness-indicator-must-measure-the-thing-you-actually-read]] for the
same shape one layer over.
