---
id: a-full-zap-inside-the-workspace-unmounts-it
title: pmbootstrap zap inside the workspace tears down porthole's own bind mounts, and the build then refuses about a version
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "Reference host 2026-08-29, workspace container. `pmbootstrap -q -y zap` inside it left /pmb/cache_git/pmaports an EMPTY directory (the host checkout at ~/.local/var/pmbootstrap/cache_git/pmaports was intact and clean -- the mount went, not the data). Every subsequent `porthole build` printed `REFUSING: tree is Linux 7.2 but linux-postmarketos-qcom-msm8998-7.2 is .` until `porthole sandbox down && up`."
first-learned: 2026-08-29
---

**What it looks like** — a build that worked a minute ago starts refusing, and
the refusal is about something that is not wrong:

```
>> REFUSING: tree is Linux 7.2 but linux-postmarketos-qcom-msm8998-7.2 is .
```

The aport IS 7.2. The trailing `.` is the tell: `_ph_tree_matches_aport`
sources the APKBUILD for `pkgver`, got nothing, and `awk -F.` printed the
separator on its own. The version comparison is reporting a file it could not
read.

**Why** — porthole's mounts live in the CONTAINER's mount namespace.
`pmbootstrap zap` unmounts under the work dir, which takes
`/pmb/cache_git/pmaports` with it and leaves the empty directory it was
mounted over. Same class as the `/dev` binds `porthole-devnodes` re-arms after
a `sandbox down`, and the same reason: a mount is not a directory.

**The fix** — `porthole sandbox down && porthole sandbox up`. Nothing on the
host was lost; the checkout is where it always was.

**Not the same as what a build does.** `pmbootstrap build` without `--lax`
calls `zap_buildroots()`, which deletes `chroot_native` and `chroot_buildroot*`
and unmounts only those. That is routine, survivable, and re-armed
automatically ([[the-workspace-caches-kernel-compiles]]). A full `zap` is the
one that takes the mounts.

**Cost of getting it wrong** — a full zap also drops `cache_distfiles`, so the
next device-package build re-downloads the 1.7 GB vendor factory image. Reach
for `sandbox down && up` first; it fixes more and destroys nothing.

Since 2026-08-29 the refusal names the real problem instead
(`tools/ph-build.sh`, `_ph_tree_matches_aport`).
