---
id: stale-dev-package-outranks-your-build
title: A _p<timestamp> dev snapshot outranks a release, so apk installs a kernel from days ago
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: taimen AGENTS.md §1.0b; 183 stale apks found 2026-08-19
first-learned: 2026-08-19
---

`pmbootstrap build --envkernel` versions its output `<ver>_p<timestamp>-r0`.
**apk sorts `_p<timestamp>` ABOVE a plain release `<ver>-rNN`.**

So one leftover envkernel apk in the local repo wins every dependency resolution
afterwards — `pmbootstrap install`, `apk add` in the rootfs chroot,
`pmbootstrap export`, all of it. You flash a kernel from days ago while every
log line you read says you just built a fresh one.

This is not rare. On taimen, 2026-08-19, there were **183** of them in the repo,
and `pmbootstrap install` chose `6.18_p20260818144641-r0` over the `6.18-r26`
that had just been built and committed. It cost most of a night: several "the
rebuilt kernel does not boot" conclusions were drawn against a kernel that was
not the rebuilt one.

There is no apk-level fix — the comparator is doing what it documents. The only
defence is to keep them out of the repo. `tkpurge-devpkgs` moves them aside and
reindexes; `tkbuild` refuses to run while any are present.

**How to tell you have been bitten:** the version apk chose is in the
`install`/`add` command line in `pmbootstrap log`, and `apk info -W /boot/vmlinuz`
in the rootfs chroot names the package that owns the kernel actually exported.
**Check ownership, never timestamps** — a fresh `boot.img` mtime says nothing
about which vmlinuz is inside it.

Related: [[apk-info-W-wants-the-path-the-package-recorded]],
[[prove-which-kernel-answered]], [[timestamps-cannot-prove-a-build-is-fresh]].
