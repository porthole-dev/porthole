---
id: abuild-checksums-follow-source-order
title: Correct downloaded bytes fail when checksums are out of source order
scope: generic
subsystem: packaging
severity: trap
confidence: proven
evidence: https://github.com/porthole-dev/pmaports/commit/43efe9c; failed firmware job https://github.com/porthole-dev/pmaports/actions/runs/36636343428/job/109637953307. Alpine abuild default_fetch sets positional parameters from sha512sums and shifts two values per source. The local order check failed before this commit and passed after it.
first-learned: 2026-09-30
---

**Symptom** — A freshly downloaded source fails its checksum, while fetching
that same URL independently produces the expected hash.

**Cause** — `abuild`'s `default_fetch` compares sources and checksums in list
order. The filenames written beside checksum values do not drive that pairing.
In the Taimen firmware recipe, the first extra firmware source was compared
against `board-2.json`'s checksum because that line appeared second.

**What to do** — Keep checksum entries in exactly the same order as `source`.
Run `pmbootstrap checksum` after changing sources, then review the resulting
order. Do not weaken checksum verification or assume the CDN corrupted a file
before comparing the downloaded bytes independently.
