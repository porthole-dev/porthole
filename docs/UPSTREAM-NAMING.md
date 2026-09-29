# Nura naming and upstream compatibility

The upstream project announced the name Nura on 2026-09-27. Its current public
site is [nura.eco](https://nura.eco); current developer documentation is at
[docs.nura.eco](https://docs.nura.eco). The official announcement says the
project is now known as Nura and that older names will take time to disappear
from user-facing strings:
[Project rebrand: Nura](https://nura.eco/blog/2026/09/27/nura-rename/).

## What we checked

On 2026-09-28, these official GitLab URLs still resolved and advertised `main`:

| Repository | Git URL | Observed main commit |
| --- | --- | --- |
| pmaports | `https://gitlab.postmarketos.org/postmarketOS/pmaports.git` | `a86395a4dfef98513dcf6d6bbe1527d1ede2e37b` |
| pmbootstrap | `https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git` | `266f37fdb4baf9507c785221bf999fe62728d0e4` |

These are observations, not permanent pins. Check `git ls-remote` before changing
upstream remotes. The old `github.com/nura-os/pmaports` location returned
“Repository not found” during the same check; do not guess replacement URLs.

Nura's [PMCR-0006 migration plan](https://docs.nura.eco/pmcr/main/0006-name-change-amendment.html)
explicitly keeps developer repositories such as pmaports and pmbootstrap, their
include paths, and compatibility aliases. It calls for user-facing strings to
move gradually. The legacy package mirror path also remains a compatibility
requirement. The Nura brand therefore does not make old source URLs, APK names,
channel names, or package repository paths safe to rename.

## Porthole usage

- Website headings and device download pages say **Nura** and list the Pixel 2
  XL only. The page describes an independent port, not an official Nura image.
- Retain `postmarketOS` in GitLab URLs, `pmbootstrap`, package names, the
  `pmos-packages` repository, release tags, and config keys while upstream uses
  those interfaces. They are identifiers, not the port's display name.
- Keep original upstream attribution and history. Do not bulk-rewrite kernel
  patches, package metadata, logs, or evidence: changing old evidence would
  obscure what was tested at the time.
- Prefer the new official documentation domain for new links. Keep old source
  links that still resolve, and change them only when upstream documents a
  stable replacement.
- Check current Nura branding before each public website release. Add old/new
  aliases only when upstream documents the actual migration.

This cleanup changes downstream presentation only. It does not claim Nura has
renamed every repository or package, and it does not imply endorsement.
