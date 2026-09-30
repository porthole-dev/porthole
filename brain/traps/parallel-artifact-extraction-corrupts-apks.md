---
id: parallel-artifact-extraction-corrupts-apks
title: Parallel artifact extraction corrupts APK payloads
scope: generic
subsystem: ci
severity: trap
confidence: proven
evidence: https://github.com/porthole-dev/pmaports/actions/runs/36661078089; https://github.com/porthole-dev/pmaports/actions/runs/36662632434; https://github.com/porthole-dev/pmaports/commit/eb4d712ac9bcb3bbdd40aacfe34a493518e2e477
first-learned: 2026-09-30
---

**Symptom** — A signed repository indexes packages successfully, but image
installation reports `unexpected end of file`, `v2 package format error`, or
`file format is invalid or inconsistent` for their payloads.

**Observed** — Both the Settings and Phosh jobs uploaded Settings APKs with the
same filename. Each original artifact passed Alpine's `apk verify
--allow-untrusted`; the merged public asset had different bytes and failed.
Six public Settings and libcamera APKs failed native verification. A gzip check
alone missed the Settings package with one trailing zero byte.

**Cause** — `download-artifact` with `merge-multiple: true` extracted competing
dependency builds into the same paths. Metadata indexing did not verify their
complete payloads. Attesting the merged files faithfully described corrupt
bytes rather than establishing installability.

**What to do** — Upload only packages whose `origin` matches the matrix job's
requested aport. Verify APK payloads before publishing, compare downloaded
assets against upload bytes, and remove malformed retained candidates from the
new index before deleting their assets. Valid older versions stay available.

**Control** — The ownership check executes the production branch with both a
foreign dependency and the requested origin. Publisher checks reject a new
invalid payload and remove a retained invalid candidate while keeping valid
history. Native verification of the original artifact versus public asset
separates a working build from a broken publication step.
