# Release pipelines

Follow builds from source checks to published artifacts. GitHub badges show the
latest workflow result; open a pipeline for its running jobs and logs.

## Live workflow status

| Pipeline | Latest result | Runs and logs |
|---|---|---|
| Packages and signed APK publication | [![Packages and signed APK publication](https://github.com/porthole-dev/pmaports/actions/workflows/build.yml/badge.svg?branch=taimen-bringup)](https://github.com/porthole-dev/pmaports/actions/workflows/build.yml) | [Open pipeline](https://github.com/porthole-dev/pmaports/actions/workflows/build.yml) |
| Package source and workflow checks | [![Package source and workflow checks](https://github.com/porthole-dev/pmaports/actions/workflows/ci.yml/badge.svg?branch=taimen-bringup)](https://github.com/porthole-dev/pmaports/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/pmaports/actions/workflows/ci.yml) |
| Complete device images | [![Device image](https://github.com/porthole-dev/pmaports/actions/workflows/image.yml/badge.svg?branch=taimen-bringup)](https://github.com/porthole-dev/pmaports/actions/workflows/image.yml) | [Build, verification, and publication](https://github.com/porthole-dev/pmaports/actions/workflows/image.yml) |
| Website deployment | [![Website deployment](https://github.com/porthole-dev/porthole/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/porthole-dev/porthole/actions/workflows/docs.yml) | [Open pipeline](https://github.com/porthole-dev/porthole/actions/workflows/docs.yml) |
| Porthole checks | [![Porthole checks](https://github.com/porthole-dev/porthole/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/porthole-dev/porthole/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/porthole/actions/workflows/ci.yml) |
| Obscura release | [![Obscura release](https://github.com/porthole-dev/obscura/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/porthole-dev/obscura/actions/workflows/release.yml) | [Open pipeline](https://github.com/porthole-dev/obscura/actions/workflows/release.yml) |
| Tap release | [![Tap release](https://github.com/porthole-dev/tap/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/porthole-dev/tap/actions/workflows/release.yml) | [Open pipeline](https://github.com/porthole-dev/tap/actions/workflows/release.yml) |
| Phosh NFC release | [![Phosh NFC release](https://github.com/porthole-dev/phosh-nfc-quick-setting/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/porthole-dev/phosh-nfc-quick-setting/actions/workflows/release.yml) | [Open pipeline](https://github.com/porthole-dev/phosh-nfc-quick-setting/actions/workflows/release.yml) |
| Firmware integrity | [![Firmware integrity](https://github.com/porthole-dev/firmware-google-taimen/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/porthole-dev/firmware-google-taimen/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/firmware-google-taimen/actions/workflows/ci.yml) |
| pmbootstrap checks | [![pmbootstrap checks](https://github.com/porthole-dev/pmbootstrap/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/porthole-dev/pmbootstrap/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/pmbootstrap/actions/workflows/ci.yml) |
| Chromium checkpoints | [![Chromium checkpoints](https://github.com/porthole-dev/pmaports/actions/workflows/chromium.yml/badge.svg?branch=taimen-bringup)](https://github.com/porthole-dev/pmaports/actions/workflows/chromium.yml) | [Open pipeline](https://github.com/porthole-dev/pmaports/actions/workflows/chromium.yml) |
| Upstream package drift | [![Upstream package drift](https://github.com/porthole-dev/pmaports/actions/workflows/upstream-check.yml/badge.svg?branch=taimen-bringup)](https://github.com/porthole-dev/pmaports/actions/workflows/upstream-check.yml) | [Open pipeline](https://github.com/porthole-dev/pmaports/actions/workflows/upstream-check.yml) |
| Shared organization automation | [![Shared organization automation](https://github.com/porthole-dev/.github/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/porthole-dev/.github/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/.github/actions/workflows/ci.yml) |
| APK repository checks | [![APK repository checks](https://github.com/porthole-dev/pmos-packages/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/porthole-dev/pmos-packages/actions/workflows/ci.yml) | [Open pipeline](https://github.com/porthole-dev/pmos-packages/actions/workflows/ci.yml) |

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

- [Installable image candidates](../images/)
- [Signed APK repositories](../packages/)
- [Obscura releases](https://github.com/porthole-dev/obscura/releases)
- [Tap releases](https://github.com/porthole-dev/tap/releases)
- [Phosh NFC releases](https://github.com/porthole-dev/phosh-nfc-quick-setting/releases)
- [Firmware sources and grant](https://github.com/porthole-dev/firmware-google-taimen)

To receive GitHub notifications, open the relevant repository and choose
**Watch → Custom → Releases**. Workflow failures and running jobs are visible
under its **Actions** tab.

## Trigger a release

Maintainers can use **Actions → workflow → Run workflow**, or the GitHub CLI.
Select the primary branch. Branch pushes and pull requests run checks; they do
not deploy Pages or publish APKs. Pmaports uses `taimen-bringup` as its primary
branch; the other maintained repositories use `main`.

```sh
# Changed core packages publish automatically after merging. Build selected ones:
gh workflow run build.yml -R porthole-dev/pmaports --ref taimen-bringup -f packages="tap obscura"
# Full image: explicit build using the signed packages already published:
gh workflow run image.yml -R porthole-dev/pmaports --ref taimen-bringup -f device=google-taimen
# Add a complete bundle to an existing candidate without rebuilding its images:
gh workflow run bundle.yml -R porthole-dev/pmaports --ref taimen-bringup -f tag=google-taimen-candidate-6 -f device=google-taimen
# Website refresh:
gh workflow run docs.yml -R porthole-dev/porthole --ref main
# Application release: the version tag must already point to a commit on main:
gh workflow run release.yml -R porthole-dev/tap --ref main -f tag=v0.2.0 -f dry-run=true
```

The application command rehearses an existing version tag; use `dry-run=false`
when ready to publish that version. Creating a `v*` tag on a commit merged into
`main` also triggers publication. A tag on an unmerged branch is rejected.

Device image checks evaluate primary-branch pushes and pull requests; expensive
image assembly remains explicit. The firmware grant must be approved. Existing
candidates and hardware claims are preserved. Core and staged Chromium APK
publishers and image/bundle workflows refresh the website after verified upload;
the scheduled refresh remains a fallback. For immediate refresh, configure the
`WEBSITE_TOKEN` secret in pmaports' `publish` environment: a fine-grained token
with only Actions write access to `porthole-dev/porthole`. The package publisher
credential remains scoped to package publication. If no website token is set,
the release summary explains that the six-hour scheduled Docs refresh will
update the catalog. A configured but invalid token fails visibly.
