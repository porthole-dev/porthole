---
id: apk-artifact-directory-appears-after-build
title: Package build succeeds but the tool reports its APK missing
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-09-29 porthole pkg build phosh returned a missing-APK error after 946 successful build steps; the artifact existed at .run/release-pmb/packages/systemd-edge/aarch64/phosh-99990.57.0-r26.apk while the tool reported looking in packages/edge/aarch64. lib/porthole_cmd_pkg.py now resolves the path again after building; tests/test_pkg.py and the actual artifact check pass.
first-learned: 2026-09-29
---

**Symptom** — `porthole pkg build phosh` finishes successfully and then says
the APK is missing from `packages/edge/aarch64`.

**Cause** — Before the build, no APK exists. The expected-path helper picks
the first repository directory as a fallback. Phosh belongs to
`systemd-edge`, which pmbootstrap creates or fills during the build. Keeping
the pre-build fallback as the post-build location checks the wrong directory.

**What to do** — Resolve the expected path again after pmbootstrap returns,
then inspect the APK's existence and timestamp. A successful process exit or
a pre-build path guess alone does not prove where the package landed.
