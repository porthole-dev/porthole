---
id: kernel-7-2-rebase-is-cheap
title: The 188-patch series rebases onto v7.2 with 16 small conflicts
scope: soc:msm8998
subsystem: kernel
severity: finding
confidence: proven
evidence: "BOOTED on the device 2026-08-29: `Linux version 7.2.2 #1-postmarketos-qcom-msm8998-7.2`, 130 modules loaded, 0 failed units, display and wlan0 up. git am -3 of the 188 aport patches onto pristine v7.2: 150 apply, 38 fail, of which only 16 are root conflicts and none exceeds 6 hunks in 3 files. Branch tk72/probe-am in linux/ is the result (144 commits). Raw census: git am each patch, --skip on conflict, count files/markers."
refutes: "moving off 6.18 means rewriting the series; the patch stack is too far from mainline to rebase; the defconfig will not survive four releases"
first-learned: 2026-08-28
confirmed: 2026-08-29 (booted)
---

**The question** — can this port move off v6.18 onto current mainline (v7.2,
released 2026-08-16), and how much of the 188-patch series survives?

**The answer** — textually, most of it. Replaying the aport series onto
pristine `v7.2` with `git am -3`:

| outcome | count |
|---|---|
| applies clean | 150 |
| fails | 38 |
| ...of which **root** conflicts (first failure in that file) | **16** |
| ...of which cascade (a later patch on a file whose root was skipped) | 22 |
| already upstream in 7.2 — applies to nothing, drop from the series | 6 |

No root conflict is larger than 6 conflict hunks across 3 files. The clusters:

- **Kconfig/Makefile context only** (trivial): 0012 imx179, 0024 tas2557,
  0150 interconnect-msm8998. Each of these cascades 1–6 follow-ups, which is
  why the raw failure count is 38 and the real count is 16.
- **Upstream refactor, one-line re-aim**: 0032 a5xx (`config->info->funcs`
  moved to the catalogue), 0106/0107 camss-vfe.h, 0121/0122 drm/msm.
- **Upstream changed the same logic** — needs judgement, not just context
  fixing: 0035 camss-csiphy (upstream now has its own
  `csiphy_match_clock_name`), 0049 dpu\_encoder\_phys\_cmd (upstream reworked
  the `is_started` condition our patch also touches), 0054 nxp-nci,
  0146/0155 pcie-qcom, 0157 irq-qcom-mpm.
- **Mechanical but not one line**: 0020 qrtr — `mod_devicetable.h` was split
  into `include/linux/device-id/*.h` treewide, so `qrtr_device_id` needs a
  `device-id/qrtr.h` of its own.
- **The CPRh/OSM cluster**: 0073 is the only root (4 hunks in
  `qcom-cpufreq-hw.c`); 0090–0094, 0110, 0112, 0117, 0118, 0120, 0187 all
  cascade off it.

Already upstream in 7.2, so delete: 0123 (`drm/msm: always recover the gpu`)
and ath10k 0124, 0125, 0126, 0127, 0130.

The defconfig also survives. Of the 1488 symbols the aport config enables, only
17 stop existing between v6.18 and v7.2 — `ARM64_LSE_ATOMICS`, `ARM64_PAN`,
`CRYPTO_*` reorg, `GENERIC_TIME_VSYSCALL` and friends. All are upstream making
something unconditional or renaming a crypto library; `make olddefconfig`
absorbs every one. Nothing device-critical disappears.

**What this rules out** — that the series has drifted too far from mainline to
follow it, and that a base bump means re-deriving the bring-up work. It does
*not* rule out the real risk, which is behavioural rather than textual: between
v6.18 and v7.2 upstream landed 96 commits in `drm/msm/disp/dpu1`, 78 in
`sound/soc/qcom/qdsp6` and 37 in `media/platform/qcom/camss` — exactly the
three subsystems this port's display, audio and camera results were measured
on. The patches applying says nothing about those results still holding.

By contrast `msm8998.dtsi` moved 24 lines in four releases (GIC_SPI macros,
RPMPD_\* indices) and every one of the port's DT patches applied clean.

**How it was established** — `git worktree add --detach <tmp> v7.2`, then
`git am -3` each patch from the source= list of
`pmaports/device/testing/linux-postmarketos-qcom-msm8998-6.18/APKBUILD` in
order, `git am --skip` on failure, recording conflicted files and `<<<<<<<`
counts. Root-vs-cascade was separated by file overlap with an earlier skipped
patch. Result saved as `tk72/probe-am` in `linux/`. Re-run it against a newer
tag to refresh; it takes about three minutes and touches no device.

Aport-side changes the move needs, none of them large: `pkgver`, the tarball
path `v6.x/` → `v7.x/` (7.2 and 7.2.2 tarballs both exist at
`cdn.kernel.org/pub/linux/kernel/v7.x/`), `_flavor` rename, and the one
`depends=` line in `device-google-taimen/APKBUILD`.


## Outcome, 2026-08-29 — it boots

Rebased onto **v7.2.2**, 178 commits, and the device runs it:

```
Linux version 7.2.2 (pmos@fedora) (Alpine clang version 22.1.8, LLD 22.1.8)
  #1-postmarketos-qcom-msm8998-7.2
130 modules loaded · 21 of msm/ath10k/q6/snd_soc/qrtr · 2 DRM cards · wlan0 up
0 failed systemd units
```

The `dmesg` error classes are the same familiar ones (qcom_smgr QMI buffering,
wcd934x mbhc impedance with nothing plugged, ASoC "mux ... has no paths",
q6v5 "Handover signaled"). No panic, no SMMU fault, no GPU fault.

### The conflicts were the easy half

Sixteen resolved conflicts got the series to apply. **Seven API changes that no
conflict revealed** then broke the build, and only a compile found them:

| gone in 7.2 | replacement |
|---|---|
| `snd_soc_kcontrol_component()` | `snd_kcontrol_chip()` |
| `snd_soc_dapm_kcontrol_dapm()` | `snd_soc_dapm_kcontrol_to_dapm()` |
| `.pcm_construct` | `.pcm_new` |
| `apr_resp_pkt *` in apr callbacks | `const apr_resp_pkt *` |
| `drm_panel_init()` (now static) | `devm_drm_panel_alloc()` |
| `qcom_ep_reset_{assert,deassert}()` | `qcom_pcie_perst_{assert,deassert}()` |
| camss `regulators[]` as `char *` | `struct regulator_bulk_data` |

Each was folded into the commit that introduces the code, so the series stays
bisectable. **A clean `git am` says nothing about whether the result compiles**
— budget a build-and-fix loop, not just a conflict-resolution pass.

### Two config traps, both silent

- `BOOTPARAM_SOFTLOCKUP_PANIC` and `BOOTPARAM_HUNG_TASK_PANIC` became `int`
  (default 0). `olddefconfig` on the old config therefore turns both
  panic-on-hang nets **off** without a word. Set them to 1 explicitly.
- `DEBUG_INFO_BTF` silently drops if `pahole` is absent when the config is
  regenerated, because its `depends on` is a `$(success,...)` probe.

### The move needed a new rung

`fast` cannot change kernel flavor: `_ph_install_kernel_release` does a plain
`apk add`, and two kernel aports own `/boot/vmlinuz` and every dtb. The add
fails on file conflicts *and registers the new package anyway*, so a later swap
purges the incumbent while apk unpacks nothing — `/boot/vmlinuz` ceases to
exist and the export packs an image with no kernel. See
[[porthole-build-upgrade-rung]]. `porthole build upgrade` now does the swap as
one apk transaction (`!oldpkg`), verifies by file ownership rather than exit
code, repairs a half-applied target first, and pushes modules **before** it
flashes — because on a major bump `/usr/lib/modules/<release>` is *absent* on
the phone, not stale, and a boot-only flash strands it with no network to fix
it over.
