---
id: build-mod-against-an-aport-kernel-fails-at-the-next-boot
title: build mod from the tree against a kernel that ships from the aport is refused by MODVERSIONS -- and on a no-reload module the refusal lands at the next boot, with the shipped module already gone
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: taimen 2026-09-02. `porthole build mod drivers/media/platform/qcom/venus/venus-dec.ko venus_dec --yes` with PORTHOLE_KERNEL_TREE=linux-ws (.output configured from the scratch taimen_defconfig) against kernel #26 (aport r25). Installed, not reloaded (no-reload list). Next boot: 'venus_dec: disagrees about version of symbol hfi_session_process_buf', modprobe EINVAL, no /dev/video7, hardware decode gone. Recovered by extracting venus-dec.ko from the r25 apk in the workspace packages dir and pushing it. The same change as series patch 0207 + `build fast` (8m32s) loaded fine as #28. porthole-dev/porthole#37.
first-learned: 2026-09-02
---

The rung table says the `mod` rung is safe against an aport kernel because
MODVERSIONS refuses a mismatch loudly. That is true only when the module is
actually reloaded. A module on `PORTHOLE_MOD_NO_RELOAD` is written over the
shipped copy and *not* loaded; the refusal comes at the next boot, when the
old copy is already gone and nothing on the phone can restore it.

The mismatch itself is ordinary: the aport builds with
`config-<pkg>.<arch>` in pmaports, the tree's `.output` with whatever
defconfig it was last configured from, and a config difference is a CRC
difference. Before a `mod` push of a no-reload module on a device that
ships from the aport, check `tk-modcrc.py` against the running kernel, or
skip straight to the `fast` rung with the change in the series -- eight
minutes, and it lands where the phone actually runs kernels.

Recovery when it has already happened: the apk that built the running
kernel is in `~/.local/var/porthole-sandbox/packages/edge/aarch64/`; it is
a tar.gz, `usr/lib/modules/<ver>/kernel/.../<mod>.ko` inside; push and
`modprobe`.
