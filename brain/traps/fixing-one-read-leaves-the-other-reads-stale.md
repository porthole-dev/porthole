---
id: fixing-one-read-leaves-the-other-reads-stale
title: Fixing one stale read leaves the other reads in the same function stale
scope: generic
subsystem: tooling
severity: trap
confidence: proven
evidence: 2026-09-10. #84 fixed `pkg drift`'s VERSION comparison to read `origin/master` instead of the 1009-commit-behind worktree, wrote brain/traps/a-freshness-indicator-must-measure-the-thing-you-actually-read about it, and left the PATCH subtraction eight lines below globbing that same worktree. Measured: main/mesa on disk carries llvm22-armhf.patch, origin/master deleted it, so `ours - theirs` subtracted a patch upstream no longer ships. drift reported "3 patches at risk" for a month; the honest answer was four. #84's own PR body asserted the opposite -- "it can overstate risk but not mask it".
first-learned: 2026-09-10
---

**When you fix a stale read, grep the whole function for the other reads of
the same object before you write the PR body claiming it is fixed.**

[[a-freshness-indicator-must-measure-the-thing-you-actually-read]] is the note
#84 wrote about this exact object. It is correct. It was also written in the
same commit that left a second read of that object stale, eight lines below
the one it fixed:

    theirs_text = read_upstream_apkbuild(upstream, up_rel_path, ref)   # the ref
    ...
    their_patches = {p.name for p in up_dir.glob("*.patch")}           # the DISK

So the version comparison asked what upstream has now, and the patch
subtraction asked what upstream had in August. On the one fork the whole
design exists for, those two disagree.

**The failure direction was also asserted wrongly, and that is the part worth
keeping.** #84's PR body reasoned: "It fails open -- an unreadable upstream
means all our patches count, never zero -- so it can overstate risk but not
mask it." That is sound for an *unreadable* upstream and false for a *stale*
one. Unreadable gives an empty `their_patches`, so nothing is subtracted and
everything is reported. Stale gives a **larger** `their_patches` than the truth,
because it still holds files upstream has since deleted -- and every one of
those is subtracted out of the alarm. Stale masks. Unreadable does not. They
are not the same failure and reasoning about one does not cover the other.

The check that would have caught it costs nothing: two tools reading the same
object must agree about it. `pkg rebase` said `llvm22-armhf.patch
upstream-deleted` while `pkg drift` did not list it at all. Nobody compared
the two outputs, because each looked reasonable alone.

So: after fixing a read, `grep` the function for every other access to that
object; and when two commands report on the same thing, run both and diff the
answers. See also [[an-unenforced-rule-is-a-defect-not-documentation]] --
a claim in a PR body that no test backs is the same shape of decoration.
