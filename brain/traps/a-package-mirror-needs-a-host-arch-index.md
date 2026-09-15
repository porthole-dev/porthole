---
id: a-package-mirror-needs-a-host-arch-index
title: A package mirror needs a host-arch index, even for a phone port
scope: generic
subsystem: packaging
severity: trap
confidence: proven
evidence: "2026-09-15/16, pmbootstrap 3.11.1 in porthole-sandbox (x86_64 host), throwaway work dir /pmb/mirrortest, mirrors.pmaports_custom and systemd_custom pointing at a local HTTP copy of the porthole-dev/pmos-packages releases, which published aarch64 only. `pmbootstrap build --arch aarch64 mesa libcamera device-google-taimen phosh` resolved mesa, libcamera and phosh from the mirror, then at `Initializing x86_64 buildroot` logged `APKINDEX outdated (file does not exist yet): http://127.0.0.1:8123/systemd/main/x86_64/APKINDEX.tar.gz`, `WARNING: file not found`, `ERROR: getting APKINDEX from binary package mirror failed!`. After signed EMPTY x86_64 indexes were added to both trees, the same resolution in /pmb/pkgrepo-e2e fetched `Update package index for x86_64 (7 file(s))`, created the native chroot, and `apk policy` picked mesa-dri-gallium 26.2.2-r51 and libphosh 99990.57.0-r24 from the mirror (tests/test_pkgrepo_workspace.py). Code path: pmb/helpers/repo.py update() raises NonBugError on any failed download unless PMB_APK_FORCE_MISSING_REPOSITORIES=1 or the arch is not in Arch.supported_binary()."
first-learned: 2026-09-15
---

**Symptom** — a custom binary repository that publishes only the device arch
(aarch64) resolves fine at first, then a build dies with
`ERROR: getting APKINDEX from binary package mirror failed!` and
`NOTE: check the [mirrors] section in 'pmbootstrap config'`. The URL named in
the log above it is the **x86_64** index, an arch nobody ever meant to publish.
It appears only once something needs the native chroot, so it looks
intermittent: a build whose packages all come from the mirror can pass, and the
next one that has to compile anything fails.

**Cause** — `pmb/helpers/repo.py` `update(arch)` downloads the index of every
configured mirror for each arch it prepares, and a native chroot is always the
host arch. Any 404 is fatal, including for a mirror nothing will be installed
from in that chroot. The escape hatch `PMB_APK_FORCE_MISSING_REPOSITORIES=1`
ignores all missing indexes, so it would also hide a missing aarch64 one.

**What to do** — publish a signed index for the host arch in every tree
(`main/x86_64`, `systemd/main/x86_64`), even an empty one: `apk index` with no
packages, signed with the same key. `porthole doctor --all` fetches the device
and host indexes anonymously and reports `host-arch-missing` when only this one
is absent. `porthole sandbox up` leaves the mirror out of the workspace config
until the probe passes, so builds keep working meanwhile.

A private GitHub repository fails in the same place with a 404 on every index,
because GitHub answers anonymous release downloads of a private repo with 404,
and neither pmbootstrap nor apk can send a token. doctor reports that case as
`private`. See `docs/CONFIG.md#package-repository` and
[[apk3-reads-only-etc-apk-keys]].
