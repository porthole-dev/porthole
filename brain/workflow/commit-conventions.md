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
- `Signed-off-by:` on every commit **bound for upstream** (DCO). The Linux
  kernel and pmaports both require it, and it is a legal assertion rather than a
  formality — which is exactly why **only the person signing may add it.** Never
  add a sign-off on someone else's behalf, and if you are an assistant, never
  add one at all.

  A project's *own* repository may want no trailers whatsoever; porthole is one
  such, see its `AGENTS.md`. That is a separate question from what an upstream
  submission needs, and the two are easy to conflate.
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
that means Alpine conventions — **`pmaports/COMMITSTYLE.md` is authoritative**,
and it follows Alpine's aports COMMITSTYLE with these pmOS additions:

- Packages under `device/` **omit the directory prefix**:
  `device-google-taimen: description` — not `device/testing/…`. The SoC form
  (`soc-qcom-msm8998: …`) and the short form (`google-taimen: …`) are both valid.
- Packages under `extra-repos/systemd/`: `systemd: …` or `systemd/$pkgname: …`.
- Moving a device between categories:
  `manufacturer-codename: move from testing to community`.
- Forking from Alpine: `pmbootstrap aportgen --fork-alpine <pkg>`, then
  `temp/<pkg>: fork from Alpine`. **One commit per forked package.**
- A new device: `manufacturer-codename: new device`, **one device per commit**,
  with its device-specific kernel and firmware packages in that same commit.
- Do not put `pkgrel` bumps in the subject. Describe the change; the bump is an
  implementation detail of the same commit.

For what a kernel aport should be *based on*, see
[[base-a-kernel-aport-on-a-pinned-tag-not-a-vendor-fork]].

## On AI attribution

Whether to add a `Co-Authored-By` trailer for an AI assistant varies by
project. **In this repository it is banned outright** — on a commit message and
on a pull request body alike, along with `Signed-off-by:`, `Claude-Session:`,
any generated-with line and any bare session URL. See AGENTS.md section 5;
`lib/porthole_trailers.py` is what enforces it.

This paragraph used to say the trailer was "the human's call", which
contradicted AGENTS.md and gave an agent reading only this note a rule that
said yes. That drift is the point of the note, not a footnote to it.

What does not vary anywhere: **omit it on commits bound for upstream kernel or
distribution trees**, where it can make review harder — which defeats the point
of upstreaming.

Related: [[90-upstreaming]].
