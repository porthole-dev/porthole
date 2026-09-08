---
id: a-dev-snapshot-blocks-install-and-says-nothing
title: An envkernel _p snapshot blocks every install, and nothing reports it until a build refuses twelve minutes in
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Reference host 2026-09-08. `ls $PMB/packages/edge/aarch64 | grep -c _p2026` returned 7 while `porthole doctor`, `porthole next` and `porthole build`'s preview all reported nothing wrong. `porthole build purge` moved them to .stale-devpkgs/ (which then held 23, the rest from earlier purges) and the repo went to 0. The rootfs chroot was separately clean: `pmbootstrap chroot -r -- apk info -W /boot/vmlinuz` reported linux-postmarketos-qcom-msm8998-7.2-7.2.2-r31, a release."
refutes: "a clean `porthole doctor` means a clean install can run; `porthole build purge` is housekeeping you run when you feel like it; purging the repo is enough to clear a _p snapshot"
first-learned: 2026-09-08
---

**The state.** apk sorts `_p<timestamp>` ABOVE `-rNN`, so an envkernel dev
snapshot left in the local repo wins against the release the world file asks
for, and `pmbootstrap install` resolves to it. `_ph_assert_no_devpkgs` refuses
the build when that happens — correctly, and only once the build has started.

**The defect is not the refusal. It is that nothing asks the question early.**
On 2026-09-08 seven snapshots had been sitting in the repo since 2026-08-31.
`porthole doctor` was clean. `porthole next` was clean. The build preview
listed every rung as runnable. The only surface that knew was a shell function
that runs after the work begins, and a `porthole flash --yes` on that host had
already failed for an unrelated reason without ever reaching it.

So "can this host do a from-scratch install today" had no answer anywhere, and
the honest answer was no.

**Purging the repo is not sufficient, and this is the half that gets missed.**
`_ph_assert_no_devpkgs` has a second check: a `_p` kernel already INSTALLED in
the rootfs chroot outranks every release, so `apk add -U -u` will not replace
it — an upgrade request cannot downgrade `-r28` from `_p20260820013151-r0`.
Measured 2026-08-20: the repo was clean, `tkpurge-devpkgs` reported zero, and
`pmbootstrap export` still shipped a kernel from an 01:31 envkernel build with
every config symbol added that morning missing. `apk info -W /boot/vmlinuz` is
what catches it. Timestamps do not.

**What to do.** Both questions belong in preflight, before a rung is chosen:

    ls "$PMB/packages/edge/$ARCH"/*_p*.apk                      # the repo
    pmbootstrap chroot -r -- apk info -W /boot/vmlinuz          # the chroot

A check that only reads the repo reports clean on exactly the case that cost a
session. Related: [[the-lock-says-who-not-what]].
