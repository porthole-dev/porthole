---
id: timestamps-cannot-prove-a-build-is-fresh
title: A fresh boot.img mtime says nothing about which kernel is inside it
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: taimen tools/taimen-build.sh header; porthole tools/ph-build.sh
first-learned: 2026-07-25
---

Two failure modes in the envkernel loop are **invisible in timestamps**, and
both produce a new `boot.img` wrapped around an old kernel:

1. `pmbootstrap build --envkernel` can abort on a stale `umount .output/Makefile`
   (exit 32) **after** writing the `.apk` but **before** refreshing APKINDEX.
   `install` then resolves the kernel from a stale index and packs an old
   vmlinuz behind a fresh `boot.img` mtime. So `index` must run chained, and the
   exit codes of `build`/`index` are deliberately ignored — **they lie**.
2. `index` must run while the chroot from `build` is **still mounted**:
   pmbootstrap bind-mounts the abuild signing key only while the chroot is
   active, so a standalone `pmbootstrap index` after a shutdown fails with
   "can't cd /mnt/pmbootstrap" / "No private key found". Never split them.

Because neither is visible in file times, the build must verify *contents*:
`tkbuild` ends by comparing the DTB inside the exported `boot.img` against the
one `make` just produced, and refuses to report success if they differ.

**Generalise: when a pipeline can produce a fresh-looking artefact around stale
content, the only honest check compares content, not metadata.**

Related: [[stale-dev-package-outranks-your-build]],
[[stacked-bind-mounts-break-pmbootstrap]].
