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

  porthole's own repository requires it on every pull request commit as well,
  and CI checks that the author signed their own commits (AGENTS.md section 5).
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

Credit an AI assistant with an `Assisted-by:` trailer, placed immediately
before the human's `Signed-off-by:`. The form is the destination's: the Linux
kernel documents `Assisted-by: LLM` (Documentation/process/coding-assistants.rst)
and says AI agents must not add `Signed-off-by`; Mesa documents `Assisted-by:`
and `Generated-by:`, reserves `Co-authored-by` for humans, and asks for
commit messages and code comments in the submitter's own words. In porthole it
is `Assisted-by: Claude`, and AI.md says why.

Never credit an AI with `Co-Authored-By:` or `Co-developed-by:`, both
human-only tags, and never publish a `Claude-Session:` line, a session URL or a
generated-with line, on a commit or on a pull request body.
`lib/porthole_trailers.py` is what enforces it here.

A project with no written AI policy is asked first. A project that does not
accept AI-assisted contributions (postmarketOS is one) gets nothing from this
work, disclosed or not: `Assisted-by:` does not make it acceptable there.

This note used to say AI attribution was banned outright and should be omitted
upstream. The convention changed on 2026-09-15; undisclosed AI is the thing
upstreams object to, and the kernel and Mesa now document the disclosure.

Related: [[90-upstreaming]].
