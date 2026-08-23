---
id: commit-conventions
title: Commit conventions for a public port
scope: generic
subsystem: method
severity: technique
confidence: proven
evidence: taimen AGENTS.md §6
first-learned: 2026-08-08
---

A port that intends to reach upstream follows the destination's practice, not
its own. When in doubt, read the upstream doc and copy it.

Two rule sets apply depending on which repo you are committing to. Do not mix
them.

## Universal

- Author **and** committer are the human, correctly attributed.
- `Signed-off-by:` on every commit (DCO). This is a legal assertion, not a
  formality.
- One logical change per commit. The body explains **why**, not what.
- **Never take credit for someone else's work.** A cherry-picked commit keeps
  its author: `git cherry-pick -x` and leave the authorship alone. On a
  community port most of the early device tree is someone else's, and getting
  this wrong is both rude and a licensing problem.

## Kernel commits

Destination is mainline, or at minimum a maintained SoC tree. See
[[90-upstreaming]] for the full set.

## Distribution packaging commits

Follow the distribution's style, which is usually not the kernel's. For pmaports
that means Alpine conventions.

## On AI attribution

Whether to add a `Co-Authored-By` trailer for an AI assistant is the human's
call and varies by project. What does not vary: **omit it on commits bound for
upstream kernel or distribution trees**, where it can make review harder — which
defeats the point of upstreaming.

Related: [[90-upstreaming]].
