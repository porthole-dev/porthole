---
id: pmbootstrap-shutdown-unmounts-portholes-own-binds
title: pmbootstrap shutdown unmounts porthole's own container binds, not just pmbootstrap's chroot mounts
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Reference host 2026-09-08, the same rootless podman workspace\n  what-a-rootless-workspace-cannot-do.md was measured in. `pmbootstrap shutdown`\n  run by hand: exit 0, all 27 chroot_rootfs_google-taimen mounts gone. The very\n  next `porthole build image --yes` then failed at 0 seconds with `could not\n  read linux-postmarketos-qcom-msm8998-7.2 pkgver/pkgrel` -- an aport-resolution\n  error, not a mount error. `ls /pmb/cache_git/pmaports` inside the container\n  showed it empty; `/pmb/cache_git/pmaports` is one of podman's own bind mounts\n  set up at container creation, not a pmbootstrap chroot mount, and it sits\n  under `/pmb` same as every chroot does. Recovery needed `sandbox down` +\n  `sandbox up`, not any pmbootstrap command. A second measurement on a freshly\n  recreated container, without running shutdown: 12 mounts live under\n  `/pmb/chroot_rootfs_google-taimen` (more mid-install), and\n  `/pmb/cache_git/pmaports` is confirmed a mount that is NOT under that prefix."
refutes: "pmbootstrap shutdown is the right tool to unmount a rootless workspace's chroot before mkfs.ext4 -d runs over it; umount_all only ever touches pmbootstrap's own chroot mounts; what-a-rootless-workspace-cannot-do.md #5's /dev warning is the only hazard in shutdown()/umount_all(); scoping is unnecessary because the container is disposable anyway"
first-learned: 2026-09-08
---

**The question** — `lib/porthole_image.py`'s rootless assembler needs the
rootfs chroot's `/proc`, `/sys` and `/dev` unmounted before `mkfs.ext4 -d`
recurses it (a live `/proc` makes `mkfs.ext4` fail with "Permission denied
while opening auxv to copy" -- see the assembler's own commit). `pmbootstrap
shutdown` is the unmount pmbootstrap itself uses between commands, verified
by hand to work rootless in this same workspace. Is it the right call to make
from `_ph_assemble_image`, once per image build?

**The answer** — no. `pmbootstrap shutdown` -> `pmb.chroot.shutdown()` ->
`umount_all(...)` walks `/proc/self/mountinfo` and unmounts **every**
mountpoint under pmbootstrap's work dir (`/pmb` in this workspace), not just
the chroot passed to it. porthole's sandbox bind-mounts its own paths --
`/pmb/cache_git/pmaports` among them -- under that same `/pmb`, at container
creation, before pmbootstrap ever runs. `umount_all` cannot tell "a chroot
mount pmbootstrap made" from "a bind podman made that merely happens to sit
under the same work dir", and unmounts both. The first build after a
`shutdown` call then fails at 0 seconds, naming an aport rather than a mount:
by the time `pmbootstrap` looks for `pmaports/device/.../APKBUILD` the
directory is an empty mountpoint again, having been unmounted out from under
the running container. Recovery is a container restart (`sandbox down` +
`sandbox up`), not anything pmbootstrap offers -- there is no pmbootstrap
command that re-establishes a bind mount porthole set up, because
pmbootstrap does not know it exists.

**What this rules out** — `pmbootstrap shutdown` (or, by the same logic, any
`pmb.chroot.shutdown`/`zap`-family call with `only_build_related` unset or
scoped wider than one chroot) as a way to clean up before a filesystem
build in this workspace. It is not merely the `/dev` rbind hazard
[[what-a-rootless-workspace-cannot-do]] #5 already names -- that finding is
about individual `umount` calls failing with "not mounted" inside the
recursive `/dev` bind, which is a correctness annoyance strict-mode zap dies
on. This is a *blast-radius* hazard: even a `shutdown` that itself reports
exit 0 and looks completely clean can still have unmounted infrastructure
that has nothing to do with the chroot it was meant to clean, because
`umount_all` scopes by "under the work dir", and porthole's own binds live
under the work dir too. The fix is to scope the unmount to the chroot's own
path prefix (`<chroot>/...`, deepest mounts first, each `umount` best-effort
since the same `/dev` rbind hazard from #5 still applies at that finer
grain) and never call `pmbootstrap shutdown`, `zap`, or anything built on
`umount_all` against the whole work dir from inside a single build.

**How it was established** — measured directly on hardware during Task 2.4's
rootless-assembler hardware gate: `pmbootstrap shutdown` run by hand, verified
clean (27 mounts, all gone), ruled safe on that basis; the very next build
failed naming an aport; `/pmb/cache_git/pmaports` found empty inside the
container; a `sandbox down`/`sandbox up` cycle was needed to recover, at which
point the same build succeeded again up to the point this finding's fix
addresses. A second, separate measurement (mount count under the chroot
prefix vs. total mounts under `/pmb`, on a freshly recreated container)
confirmed the chroot-prefix scope is both sufficient (12 relevant mounts,
more mid-install) and safe (`cache_git/pmaports` is provably outside that
prefix). Overturned by: porthole's sandbox setup moving its own binds outside
`/pmb` entirely, or a pmbootstrap release that scopes `umount_all` to the
chroot path itself rather than the whole work dir.
