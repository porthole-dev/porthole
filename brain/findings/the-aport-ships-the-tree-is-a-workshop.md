---
id: the-aport-ships-the-tree-is-a-workshop
title: The aport series ships; linux/ is a topic-branch workshop, and diffing it against a checked-out branch means nothing
scope: device:google-taimen
subsystem: kernel
severity: finding
confidence: proven
evidence: audit 2026-08-27 -- 25 local branches plus 10 worktrees in linux/; HEAD was wifi-disablekey-test @ 2026-08-22 while tk618/a5xx-wake-reset and voice-split carried 2026-08-27 commits; per-branch subject comparison against the 187-patch series
refutes: the tree is frozen and no longer developed; only-tree lines in tk-reconcile output are unshipped fixes worth harvesting; a file the series never touches must be lost work
first-learned: 2026-08-27
---

**The question** — `tk-reconcile.sh` reports dozens of files differing between
the aport series and `linux/`, with hundreds of "only-tree" lines. Are fixes
being written in the tree and never shipped?

**The answer** — no, and the report cannot answer that question in the first
place. **`tk-reconcile` compares against `linux/` HEAD, and HEAD is whatever
topic branch happens to be checked out.** There are ~25 local branches and ~10
worktrees. On 2026-08-27 HEAD was `wifi-disablekey-test`, whose tip is dated
08-22, which made the whole tree look frozen. It was not: `voice-split` and
`tk618/a5xx-wake-reset` both had commits that same day. The file-level diff was
measuring "the aport versus one unrelated topic branch", which is noise.

The workflow is topic branch -> `format-patch` -> numbered series -> aport.
The **aport series is what ships** (`porthole build fast` builds it; it applies
cleanly to the shipping base; v7.2 since 2026-08-29). `linux/` is where patches are authored.

**The right audit** is per branch, on commit subjects, not per file:

    for b in $(git for-each-ref --format='%(refname:short)' refs/heads/); do
        git log --format=%s v6.18..$b        # compare against the series' Subject: lines
    done

Run on 2026-08-27 that gave: `tk618/a5xx-wake-reset` **0 unshipped**;
`series-r70` 2, both false positives from a MIME-wrapped `Subject:`; the rest
BRINGUP instrumentation (`drm/msm: BRINGUP: crumb the GPU's power path`),
DIAGNOSTIC arms (`floor VDD_MX at TURBO`), self-cancelling experiment pairs
(a commit and its own `Revert`), theories since **measured dead** (everything
VDD_MX -- see `holding-vdd-mx-does-not-stop-the-wake-crash`), and arms that are
the *next* experiment rather than a result (the devfreq pair; `HANDOFF-2026-08-28-NEXT.md`
calls it "the condition nobody has probed").

**Nothing proven-but-unshipped was found.** Not one commit.

**The trap this cost, and it is the important part** — the audit surfaced
`drivers/gpu/drm/msm/msm_mdss.c`, forty uncommitted lines that **no patch in
the series touched**: the MDSS summary IRQ is a chained handler, so
`suspend_device_irqs()` skips it and it stays live across sleep. The mechanism
is real and the code is well argued, so it looked exactly like the one fix that
had slipped through, and it was written into the series as patch 0188, built
and flashed.

It should not have been. `docs/HANDOFF-2026-08-24-BUG-A.md:40` already lists
it as **refuted, with a positive control**: "`msm_mdss`'s chained IRQ (patched
module provably loaded and it still died)". It is a tested and rejected
hypothesis, not unshipped work. Reverted in pkgrel 98.

So: **a file the series never touches is not evidence of lost work.** The
series is a record of what was *kept*, and the most interesting thing an audit
finds is usually something that was deliberately dropped. Before promoting
anything out of the tree into the series, search the handoffs and
`porthole brain` **for that specific mechanism** -- not just for the subsystem
you started in. Searching "ACD" and "cpufreq" does not surface a refutation
filed under "Bug A".

**What would overturn it** — nothing about the branch structure; it is a
description of a workflow. But re-run the per-branch audit after any long gap,
because "no proven-but-unshipped work" is a statement about a moment.
