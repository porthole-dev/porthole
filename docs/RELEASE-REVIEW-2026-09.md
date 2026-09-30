# Organization release review — 30 September 2026

The core distribution is published. [Device downloads](https://porthole-dev.github.io/porthole/images/) provides the complete Pixel 2 XL installation; [signed packages](https://porthole-dev.github.io/porthole/packages/) and [pipeline links](https://porthole-dev.github.io/porthole/pipelines/) are available on the same site.

## Published candidate

[Google Pixel 2 XL candidate 6](https://github.com/porthole-dev/pmaports/releases/tag/google-taimen-candidate-6) includes the sparse rootfs image, matching boot image, required mainline DTBO, installation instructions, checksums, exact source revisions, installed package inventory, public keys and signed repository snapshots. It includes licensed nonfree firmware and the restored NFC Settings page.

| Asset | SHA-256 |
|---|---|
| Rootfs, compressed | `6dd1f8d9380ba244270cb0c1873a6348f3a1bd4a9a48fd9ecc64a67038f3885b` |
| Boot | `d12a1679d9e719e5a468fe748edf490b32be866723111f3585aa23bdfb99a2df` |
| DTBO | `fd9752b381403312bcdeda8bb8555f0ad964f3464e7bc4ed307e3cef018362da` |

**This is an experimental candidate.** Build verification does not establish that this exact image boots on hardware. Earlier working images do not certify these bytes. Chromium remains in its staged build workflow; it is an optional browser lane.

## Verified by execution

- [Full image workflow](https://github.com/porthole-dev/pmaports/actions/runs/36665647088): installation completed; required package versions, modem firmware, NFC packages, absence of build-host authorized keys, and DTBO content passed. The decoded filesystem UUIDs match the boot cmdline; kernel and appended DTB match the installed package.
- Independent public downloads: every checksum passed. GitHub attestations verified separately for rootfs, boot and DTBO. Each installation asset answered HTTP 200 without authentication.
- Both public APK repositories: 148 main packages and 20 systemd packages passed Alpine's native payload verifier. All four repository index signatures verified against the published organization key. Image installation consumed these repositories successfully.
- Published Settings `51.0-r56`: extracted resources contain the visible NFC navigation row and NFC page definition. This verifies released UI content; NFC adapter behavior still requires a hardware test.
- Maintained repository CI, firmware verification and Obscura/Tap/Phosh NFC release workflows passed. WebKit and Epiphany APK publication passed; Chromium is still compiling.
- Local `make ci` passed. The local Python 3.8 container check was unavailable because Podman is absent; GitHub runs that compatibility gate.
- Website: internal route checks passed; the populated image page passed mobile/desktop checks in light/dark themes, without script errors or horizontal overflow. Screenshots were inspected. The live page contains every required installation download.

## Failures repaired

| Failure | Repair and control |
|---|---|
| Missing Rust compiler wrapper with prebuilt dependencies | Install sccache from declared build dependencies, including initialized chroots. Regression and real Obscura build pass. |
| Corrupt APKs despite a signed index | Competing matrix artifacts extracted into the same filenames. Publish only the requested origin; verify payloads and downloaded bytes. Remove invalid retained candidates, preserving valid history. Both original Settings artifacts passed while the merged public asset failed. |
| Missing image loop partition nodes | Create only the selected image's missing partition nodes from kernel sysfs numbers. Physical disks and existing nodes remain untouched. The corrected installer completed the filesystem image. |
| Protected rootfs checks ran without permission | Run the existing validator and udev control with privilege inside the CI container. Required content checks pass. |
| UUID verifier read an Android sparse image as raw GPT | Decode a temporary copy with the existing Android tool; preserve the original fastboot export. UUID comparison passes. |
| Local fallback test inherited desktop configuration | Isolate XDG configuration as well as HOME. The test failed before the fix and passes afterward. |

The kernel package contains `nf_tables.ko`. pmbootstrap's firewall warning is based on a missing recipe check option, not an inspection of that module; runtime firewall behavior remains part of hardware validation.

## Repository review links

Changes were reconciled against published branches. Unpublished GTK4, Stevia and Phosh theme work was preserved separately. Commit messages include the authorized human sign-off and assistant disclosure.

| Repository | Review |
|---|---|
| porthole | [Compare changes](https://github.com/porthole-dev/porthole/compare/84a388f...620c878d990877e1bca50b343f01445ba068135f) |
| .github | [Compare changes](https://github.com/porthole-dev/.github/compare/b4f7bd46f772860e6f825139e2cff7d3b25d898c...55fae9480ac477dd875ffce4471b5f5fc93cc0a6) |
| pmaports | [Compare changes](https://github.com/porthole-dev/pmaports/compare/b6dbbc1b14bba240a658c5a97fd9d256769c9aa4...ebcc7f1ae2a6ccde4d1772086e63bcbd4a269a97) |
| pmbootstrap | [Compare changes](https://github.com/porthole-dev/pmbootstrap/compare/5b42347...d4183da02b3b3030b0557e4520ade30c3311d53d) |
| pmos-packages | [Compare changes](https://github.com/porthole-dev/pmos-packages/compare/7dfa7ed...91602540efe1508c8562c507dffac4f6067c4bf3) |
| obscura | [Compare changes](https://github.com/porthole-dev/obscura/compare/eb89479...0cb479ae234373abef90a0900efbfcccb1c33a7a) |
| tap | [Compare changes](https://github.com/porthole-dev/tap/compare/c599355...8f92d6d4178078250e3f1671878e86ac4e439284) |
| phosh-nfc-quick-setting | [Compare changes](https://github.com/porthole-dev/phosh-nfc-quick-setting/compare/66fb596...84fc9ca87e3221a79026ed7dd13e8e98b46d524d) |
| firmware-google-taimen | [New device firmware repository](https://github.com/porthole-dev/firmware-google-taimen) |

The organization and repository homepages point to the website. READMEs use Nura naming while preserving upstream project names and commands where required. Firmware sources retain origin, version, device labels and redistribution documentation. The website explains the independent AI-assisted fork and credits the Nura team and other upstream projects.

## Next improvements

1. **Certify an exact image on hardware.** Record first boot, login, display/input, suspend/resume, networking, modem, camera, NFC, recovery and firewall checks against the published checksums. Promote only after those results exist.
2. **Finish Chromium publication.** Preserve staged checkpoints, repair any failed stage, then verify the resulting public APK payloads and repository index.
3. **Complete public initial-login instructions.** Make the installation guide self-contained before stable promotion.
4. **Automate website refresh after publication.** Dispatch the existing Docs workflow from successful image/package publication instead of waiting for the scheduled refresh.
5. **Make package browsing easier.** Separate current installation choices from retained versions, with the snapshot compatibility visible; keep exact-image historical downloads intact.
6. **Track cache effectiveness.** Record compiler hit rate and build time in job summaries. One warm kernel job took about five minutes with 50% cache hits; compare equivalent builds before claiming a general speedup.
7. **Expand device contracts.** Add devices through the existing image configuration, with required firmware, DTBO, package and installation checks. A profile page alone must not imply a supported release.
8. **Tighten kernel and knowledge gates.** Enable the complete nftables configuration check and resolve old knowledge markers only when their evidence can be recovered.
