---
id: image-build-cannot-find-verifier
title: Image build finishes export then cannot find verifier
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-09-27 screenshot shows tools/ph-build.sh line 1286 trying /work/tools/bootimg-verify.py; lib/porthole_cmd_sandbox.py mounts porthole at /porthole and the device worktree at /work; tests/test_ph_build.sh checks the call sites
first-learned: 2026-09-27
---

**Symptom** — `porthole build image` completes `pmbootstrap export`, then says
`/work/tools/bootimg-verify.py: No such file or directory` and refuses the image.

**Cause** — `/work` is the device worktree in the sandbox. Porthole's own tools
are mounted at `/porthole`. The build script used the worktree path for its
verifier, so the failure appeared only after the expensive install and export.

**What to do** — run a porthole revision that resolves boot image helpers from
the porthole checkout. Check the sandbox mounts with `porthole sandbox status`
before retrying. `tests/test_ph_build.sh` guards the verifier and repacker
call sites against this path mix-up.
