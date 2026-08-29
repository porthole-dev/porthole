---
id: a-stale-inherited-env-outbuilds-the-profile
title: A stale inherited env outbuilds the profile
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-08-29, taimen. Inherited env carried PORTHOLE_KERNEL_PKG=...-6.18 (porthole config showed source "environment") while profiles/google-taimen/device.env correctly said 7.2. `porthole build fast` built 6.18-r101, and apk then failed with "trying to overwrite ... owned by ...7.2-r13" on every later build until the stray package was deleted from the rootfs chroot.
first-learned: 2026-08-29
---

**Symptom** — `porthole build` builds a kernel package you retired weeks ago,
or the build fails with apk "trying to overwrite <file> owned by <the right
package>" errors after one such build got through. The profile on disk is
correct the whole time.

**Cause** — porthole config lets the environment outrank the profile, and an
agent session inherits its environment from whatever shell launched it --
possibly captured days ago. Stale PORTHOLE_KERNEL_PKG / PORTHOLE_KCONFIG_FILE
exports silently redirect the whole build.

**What to do** — `porthole config` prints the source of every value; if
something that should track the profile says `environment`, override it
explicitly for the build (`PORTHOLE_KERNEL_PKG=... porthole build fast`) or
launch from a clean shell. If the wrong package already half-installed,
`pmbootstrap chroot -r -- apk del <wrong-pkg>` unblocks apk without a zap.
