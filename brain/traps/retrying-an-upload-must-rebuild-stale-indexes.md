---
id: retrying-an-upload-must-rebuild-stale-indexes
title: Retrying an upload must rebuild stale indexes
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: https://github.com/porthole-dev/pmaports/commit/461e2ed; https://github.com/porthole-dev/pmaports/actions/runs/36668882264; https://github.com/porthole-dev/pmaports/actions/runs/36668882249
first-learned: 2026-09-30
---

**Symptom** — A publication retry finds identical package assets and reports
nothing new, but the signed index still omits those packages.

**Cause** — Uploading packages and uploading the index are separate operations.
If publication stops between them, identical assets do not establish that the
index was updated. A publisher that ignores repository download failures can
also sign a partial inventory and lose older packages from the index.

**What to do** — Compare names in the verified signed index with the complete
release APK inventory. Force reindexing on a mismatch, even if payload bytes
are unchanged. Refuse failed or incomplete downloads. Use the same publisher
for every release lane, and compare downloaded assets before declaring success.

**Control** — The production index/assets comparison runs against three local
fixtures: a matching index, an orphaned new APK, and an empty host repository.
Only the orphaned case requests reindexing. The workflow regression rejects
Chromium's old separate publisher; the shared path and its CI pass. These
controls establish retry behavior, not an observation that the specific
interrupted-upload fixture happened in production.
