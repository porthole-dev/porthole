---
id: never-flash-a-tree-built-kernel-when-the-device-ships-from-an-aport
title: Never flash a kernel built from the source tree when the device ships from an aport series
scope: generic
subsystem: kernel
severity: trap
confidence: proven
evidence: taimen 2026-08-25: spliced a linux/ tree-built Image.gz into the device's own boot.img; it burned its 3 boot retries and fell back to the bootloader. tk-reconcile.sh reported 38 files differing between the aport series and linux/ HEAD, including pcie-qcom.c, easel-mipi.c, msm-poweroff.c (64 lines), qcom_smd-regulator.c (136), irq-qcom-mpm.c (129). bootimg-verify.py had already refused an earlier image because the tree's DTB was the stale one carrying the capacity-dmips-mhz bug (1d9e2f63638a28b5) while the flashed DTB was cb6e5754f5d02acd. The tell that a device ships from the aport: uname -v equals pkgrel+1 from the APKBUILD. Fix: add the patch to the aport series, bump pkgrel, checksum, build the aport.
first-learned: 2026-08-25
---

**Symptom** — you flash a kernel you just built and the phone never comes back.
It burns its boot retries and drops to the bootloader (or goes dark), with no
kernel log of any kind, because it never got far enough to have one. The image
itself is fine: `ANDROID!` magic, right size, `bootimg-verify.py` may even pass.
Flashing the previous image back works immediately, which makes it look like the
one line you changed broke the boot.

**Cause** — the kernel that ships on the device was built from the **aport
series** (pmaports), not from the checked-out source tree. The two are allowed
to diverge and on a mature port they diverge enormously: on taimen
`tools/tk-reconcile.sh` reported **38 differing files**, including
`pcie-qcom.c`, `easel-mipi.c`, `msm-poweroff.c` (64 lines),
`qcom_smd-regulator.c` (136) and `irq-qcom-mpm.c` (129). A tree build is missing
dozens of device-critical patches, so it cannot bring up PCIe, the regulators or
the interrupt controller — it dies long before userspace. Your one-line change is
irrelevant to the failure.

The same divergence shows up earlier and more cheaply as a **DTB mismatch**:
`bootimg-verify.py` refuses the image because the tree's DTB is not the one in
the boot image. Treat that refusal as a signal about the whole kernel, not as a
DTB problem to work around.

**What to do** — check which source ships before you build anything:

    uname -v                    # e.g. #69-postmarketos-...
    grep pkgrel <aport>/APKBUILD

If `uname -v` is `pkgrel + 1`, the device ships from the aport
(`KBUILD_BUILD_VERSION="$((pkgrel + 1))-$_flavor"` is the usual APKBUILD line).
Then put the change in the aport series, not the tree:

1. write the patch into the aport directory, numbered after the last one
2. add it to `source=` in the APKBUILD
3. bump `pkgrel` — this is also your positive control, because a successful
   flash must then report the new `uname -v`
4. `pmbootstrap checksum <pkg>`
5. `pmbootstrap build --force <pkg>` — and delete any `_p` snapshot apk from an
   earlier envkernel build first, because a stale snapshot outranks a release
   build and pmbootstrap will report "up to date" instead of building

The source tree stays useful for reading and for envkernel **module** builds
(vermagic and symbol CRCs still match, so a module built there loads on the
shipping kernel). It is only the *kernel image* that must come from the aport.

Run `tk-reconcile.sh`, or the equivalent, whenever you are about to assume the
two are the same. See [[porthole-blob-tooling]] for the image-surgery tools
(`bootimg-repack-dtb.py`, `bootimg-verify.py`) that make a boot.img swap
possible without a full `pmbootstrap install`.
