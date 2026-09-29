---
id: git-ancestry-is-not-a-maintenance-inventory
title: Fork audit selects hundreds of unrelated packages
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: porthole-dev/pmaports .github/scripts/packages.py previously used an upstream merge-base to enumerate forks; local replacement .github/scripts/test-maintained.py validates the explicit inventory without any Git history; porthole release inventory resolves profile manifests against APKBUILD paths
first-learned: 2026-09-28
---

**Symptom** — an upstream audit or missing-package build selects hundreds of
packages that the project does not maintain, or fails its matrix-size limit.

**Cause** — a Git merge base describes shared history, not ownership. Rewritten
history can move that base years back while the current downstream patch set
remains small. Caching cannot repair an incorrect build selection.

**What to do** — keep maintained aports in the profile/shared manifests and
resolve those names against the actual checkout. Reject missing or ambiguous
paths. Keep changed-package detection for pull requests separate from the
maintained-package inventory. Record missing profile packages in the audit;
a smaller matrix is not success if required packages vanished from it.
