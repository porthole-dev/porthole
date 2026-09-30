# Porthole · device bring-up and downloads

[Website](https://porthole-dev.github.io/porthole/) · [Downloads](https://porthole-dev.github.io/porthole/images/) · [Device support](https://porthole-dev.github.io/porthole/devices/)

Porthole is a command-line toolbox for bringing up Linux on mobile devices.
This repository contains the tool, device profiles, build guidance, and a
separate catalogue for community-built device images.

## Get started

### Browse device status and downloads

See the current support status, available artifacts, SHA-256 checksums and
image-specific hardware evidence.

[Read device status →](docs/PROJECT-STATUS.md)

### Download an image

The download catalogue reports each device's release state, artifact size, and
SHA-256 checksum. A bring-up profile is not a promise that an installable image
is available.

[Read release availability →](docs/RELEASES.md)

### Set up a build host

Install the small set of host prerequisites, then start the rootless workspace.
Builds do not need host root.

```sh
porthole sandbox up
porthole sandbox shell --command 'pmbootstrap status'
```

[Read host setup →](docs/NEW-HOST.md)

### Build and test

Follow the working guide for the first build, package work, device setup and
troubleshooting.

[Open the working guide →](docs/WORKING-GUIDE.md)

## Device coverage

The device directory is generated from the profiles in `profiles/`. It includes
bring-up work whether or not a device is release-ready. Currently Taimen has an
explicit release policy; Cheetah has a bring-up profile without release
eligibility. The downloads page shows that distinction and never implies that
a profile alone means an image is ready. A hardware result applies only to the
exact image hash recorded with it.

## Contribute

Start with [contributing](docs/CONTRIBUTING.md), then run `make ci`. The project
keeps its own downstream changes and reports issues here; it does not represent
Nura or speak for its maintainers. Read the [project status](docs/PROJECT-STATUS.md)
for current blockers and [release guide](docs/RELEASES.md) for how artifacts and
evidence are reviewed.

Nura was announced on 2026-09-27; see [the announcement](https://nura.eco/blog/2026/09/27/nura-rename/).
Some package and repository names retain `postmarketOS` for upstream
compatibility; see [naming notes](docs/UPSTREAM-NAMING.md).

Independent downstream project. See [AI.md](AI.md) for the assistance and
contribution policy.
