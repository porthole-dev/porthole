# Project status

Porthole is an experimental Nura downstream project. Device availability, image
verification, and hardware behavior have separate evidence.

- The generated site lists device profiles and reviewed image artifacts
  separately. [Release procedure](RELEASES.md) explains why a profile does
  not imply a download.
- [Signed packages](https://github.com/porthole-dev/pmos-packages/releases)
  have their own build/publication lifecycle.
- Image and hardware gates are defined in that release procedure.
- [Package CI](https://github.com/porthole-dev/pmaports/actions) exposes current
  runs; a green package build does not certify an image or a hardware feature.

## Responsibilities

The organization maintains shared contribution policy and build infrastructure.
`google-taimen` has an experimental candidate pipeline. Promotion to a tested
release requires first-boot, login and recovery evidence for that exact image.
`google-cheetah` has a bring-up profile but no reviewed release policy. A release reviewer must have access to
the target hardware before approving working claims. A second maintainer should
be able to repeat the documented procedure before a regular release commitment.

## Weekly triage

Review failed required checks, unavailable profile packages, upstream version
drift, browser security updates, stale hardware reports, and broken documentation.
Assign release blockers in the owning repository. A device without an active
tester remains experimental or research; do not preserve a misleading green cell.

Target one successful weekly build per enabled combination, and current critical
hardware tests for each promoted image. These are operating targets, not claims
that today's project has achieved them. Publish a short changelog when a release
is promoted, linking exact manifests and known regressions.

## Hosting

Keep source hosting, compute and artifact storage independent. Ordinary package
jobs remain on GitHub. Qualify a heavy builder with a cold build, a warm build,
and an interrupted/resumed Chromium build before changing runners. Record source
revisions, CPU/RAM/disk, peak usage, elapsed time, checkpoint transfer costs and
effective monthly capacity. Provider applications and forge migration require
separate review; no cleanup script creates accounts or migrates repositories.
