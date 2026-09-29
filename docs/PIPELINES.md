# Release pipelines

Follow builds from source checks to published artifacts. GitHub badges show the
latest workflow result; open a pipeline for its running jobs and logs.

## Live workflow status

| Pipeline | Latest result | Runs and logs |
|---|---|---|
| Packages and signed APK publication | [![Packages and signed APK publication](https://github.com/porthole-dev/pmaports/actions/workflows/build.yml/badge.svg?branch=taimen-bringup)](https://github.com/porthole-dev/pmaports/actions/workflows/build.yml) | [Open pipeline](https://github.com/porthole-dev/pmaports/actions/workflows/build.yml) |
| Package source and workflow checks | [![Package source and workflow checks](https://github.com/porthole-dev/pmaports/actions/workflows/ci.yml/badge.svg?branch=taimen-bringup)](https://github.com/porthole-dev/pmaports/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/pmaports/actions/workflows/ci.yml) |
| Website deployment | [![Website deployment](https://github.com/porthole-dev/porthole/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/porthole-dev/porthole/actions/workflows/docs.yml) | [Open pipeline](https://github.com/porthole-dev/porthole/actions/workflows/docs.yml) |
| Porthole checks | [![Porthole checks](https://github.com/porthole-dev/porthole/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/porthole-dev/porthole/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/porthole/actions/workflows/ci.yml) |
| Obscura release | [![Obscura release](https://github.com/porthole-dev/obscura/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/porthole-dev/obscura/actions/workflows/release.yml) | [Open pipeline](https://github.com/porthole-dev/obscura/actions/workflows/release.yml) |
| Tap release | [![Tap release](https://github.com/porthole-dev/tap/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/porthole-dev/tap/actions/workflows/release.yml) | [Open pipeline](https://github.com/porthole-dev/tap/actions/workflows/release.yml) |
| Phosh NFC release | [![Phosh NFC release](https://github.com/porthole-dev/phosh-nfc-quick-setting/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/porthole-dev/phosh-nfc-quick-setting/actions/workflows/release.yml) | [Open pipeline](https://github.com/porthole-dev/phosh-nfc-quick-setting/actions/workflows/release.yml) |
| Firmware integrity | [![Firmware integrity](https://github.com/porthole-dev/firmware-google-taimen/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/porthole-dev/firmware-google-taimen/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/firmware-google-taimen/actions/workflows/ci.yml) |
| pmbootstrap checks | [![pmbootstrap checks](https://github.com/porthole-dev/pmbootstrap/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/porthole-dev/pmbootstrap/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/pmbootstrap/actions/workflows/ci.yml) |

## Follow a release

1. **Packages:** source checks and package builds run in pmaports. The
   publication job signs and uploads the APK indexes and packages.
2. **Applications:** version releases publish source archives, checksums,
   and provenance, then propose the matching package update.
3. **Device images:** available images and their test evidence appear in the
   [downloads](../downloads/) and [device directory](../devices/). A green
   package build does not mean an image has passed hardware testing.
4. **Website:** the Docs workflow rebuilds the portal and deploys it to Pages.

## Published artifacts

- [Signed APK repositories](https://github.com/porthole-dev/pmos-packages/releases)
- [Obscura releases](https://github.com/porthole-dev/obscura/releases)
- [Tap releases](https://github.com/porthole-dev/tap/releases)
- [Phosh NFC releases](https://github.com/porthole-dev/phosh-nfc-quick-setting/releases)
- [Firmware sources and grant](https://github.com/porthole-dev/firmware-google-taimen)

To receive GitHub notifications, open the relevant repository and choose
**Watch → Custom → Releases**. Workflow failures and running jobs are visible
under its **Actions** tab.
