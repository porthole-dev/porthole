# Building and reviewing releases

Release tooling is local and uses the existing porthole profiles, pmbootstrap
workspace, boot-image verifier, and matrix probes. It does not flash, sign,
upload, or contact a forge. GitHub builds and publishes signed packages through
the pmaports Build workflow. Its Device image workflow assembles experimental
candidates, verifies packaged kernel and DTB contents, publishes checksums and
provenance attestations, and checks the downloaded release assets. Promotion to
a tested release still requires the hardware evidence below.

## Rehearse the organization chain locally

Keep local checkouts of the organization's repositories in one directory, then
run:

```sh
porthole release rehearse --org .run/organization-cleanup --json
porthole release rehearse --org .run/organization-cleanup \
  --mirror /path/to/release-assets --manifest /path/to/manifest.json \
  --artifacts /path/to/image-files --json
```

The rehearsal lists every local workflow, runs the package publisher's own
contract test and the Docs workflow's generate/build/link commands, and checks
the image policy. With a supplied mirror it serves assets on loopback, verifies
the signed indexes for target and host architectures, and anonymously fetches
every APK named by those indexes. With a manifest and image files it checks
the recorded sizes and SHA-256 hashes. A `blocked` phase is untested, not a
pass. `listed` means a workflow exists in the checkout but its runner was not
executed; `missing` means the checkout has no workflow. The command exits 1
until the release chain is proven. GitHub release uploads, Pages deployment, provenance attestation, and
hardware boot still require real service and device evidence. The rehearsal
does not build an image or publish anything.

## Sources of truth

| Fact | Owner |
|---|---|
| Package recipe/dependencies | pmaports APKBUILD and deviceinfo |
| Device protocol/configuration | `profiles/<device>/device.env` |
| Packages maintained by a port | `profiles/<device>/aports.conf` |
| Independent packages | `profiles/shared-aports.conf` |
| Eligible combinations and blockers | `profiles/<device>/release.json` |
| Build and hardware evidence | reviewed JSON records in `releases/` |

Export the maintenance inventory for a reviewed pmaports checkout:

```sh
porthole release inventory --pmaports /path/to/pmaports \
  --output /path/to/pmaports/.github/maintained-aports.json --json
porthole release plan --pmaports /path/to/pmaports --json
```

The inventory hashes its source manifests. Review generated changes with their
profile changes. `missing` entries name unresolved inputs; they never disappear
silently. The device directory is generated from all `profiles/*/device.env`
files. A profile without `release.json` appears as bring-up work with release
eligibility unreviewed. Only profiles with reviewed release policies enter the
image plan; disabled or blocked combinations remain excluded. Enable Taimen
only after its firmware, first-boot, and package prerequisites are resolved.

## Candidate build

Build in `porthole sandbox`. Use the same pinned porthole and pmaports revisions
that the job records. Preflight must invoke the boot verifier before expensive
installation, with the toolkit and device worktree mounted at distinct paths.
Never substitute a host build when the sandbox is unavailable.

Require the selected fork packages to be available before assembly. Record exact
APKs and signed repository indexes; installation must not silently substitute a
newer upstream package. Keep all inputs needed by retained image snapshots.

The image gate compares boot content with the kernel APK actually installed,
checks rootfs/kernel/module agreement, and inspects first-boot state. Include
negative controls for a stale kernel/DTB, a wrong UUID, missing verifier, and an
empty export. A structural pass does not prove the image boots on hardware.

### Pixel 2 XL DTBO

The Taimen bootloader requires a DTBO table entry with ID 2704. The stock
overlay has that ID but cannot be applied to the mainline DTB. The Taimen
device aport now builds [Caleb's empty mainline stub](https://gitlab.com/calebccff/dtbo-google-wahoo-mainline) from its GPL-2.0-only DTS
and `dtboimg.cfg`, and installs it as `/boot/dtbo.img`. The rebuilt output
matches the 364-byte image in the device worktree byte for byte (SHA-256
`fd9752b381403312bcdeda8bb8555f0ad964f3464e7bc4ed307e3cef018362da`).

`pmbootstrap export` must provide `boot.img`, `dtbo.img`, and the rootfs image.
`porthole build image` checks the exported DTBO hash. For a full installation,
`porthole flash full` checks that same export before writing the rootfs, then
flashes boot and DTBO to both A/B slots. A release bundle must carry the DTBO
as a separate `role: dtbo` manifest artifact; the Taimen release policy rejects
a verified manifest without it. A bare rootfs image cannot program the DTBO
partition by itself. Do not flash the stock DTBO alongside the mainline boot
image. Hardware boot remains unverified in this session because the device is
absent.

Taimen's `firmware-google-taimen` APKBUILD uses a commit-pinned TheMuppets
archive for most vendor blobs. Its modem firmware and `modemr.jsn` come from
Google's factory zip; the optional fingerprint subpackage's trustlet also
comes from that zip. The port's WS-07 notes compared eight shipped
TheMuppets-sourced blobs against the Android 11 extraction and found identical
bytes. [TheMuppets' public mirror](https://github.com/TheMuppets/proprietary_vendor_google_taimen)
is a reproducible source, and [postmarketOS firmware policy](https://docs.postmarketos.org/pmaports/main/firmware.html)
supports packaging nonfree firmware. The community `firmware-google-sargo`
aport is a close precedent: it combines a pinned TheMuppets archive with a
separate modem source. Neither precedent, by itself, establishes
redistribution rights for every resulting APK or image. [Google's published
factory-image terms](https://developers.google.com/android/images) restrict redistribution; review the applicable rights for
the actual blobs before enabling public image publication.

The maintainer supplied the Google and Qualcomm redistribution grant transcript.
It is recorded with a checksummed Exhibit A in
[firmware-google-taimen](https://github.com/porthole-dev/firmware-google-taimen/blob/main/REDISTRIBUTION.md).
The additional modem and fingerprint inputs are hosted there and pinned by
commit in the aport; the existing TheMuppets source remains pinned separately.
`FIRMWARE_GRANT_TAIMEN=approved` enables publication of the Taimen base and
fingerprint APKs. Other device firmware remains outside that exception.

The organization package publisher refuses other firmware APKs and files
under `/lib/firmware`. That is a project guard, not a conclusion that all
TheMuppets-sourced firmware must be excluded. The device package still pulls
firmware into an assembled development rootfs, so excluding firmware from the
binary package repository does not make the image firmware-free.

If the rights review supports distribution, a normally functional public image
can include the reviewed blobs and should be tested as such. Otherwise, a user
may assemble an image locally with firmware they are permitted to use and keep
that image on their machine. A public firmware-free base image would require
changing the Taimen device aport so
`firmware-google-taimen` is not an unconditional dependency, then documenting
and testing a local firmware installation step after first boot. The current
aport has that unconditional dependency, so excluding firmware APKs from the
package repository alone cannot produce a releasable firmware-free image.
Do not mark `checks.firmware` true until the resulting rootfs has been
inspected for blob paths and license obligations.

A firmware-free Taimen base must not be advertised as a normally working
phone. The current firmware package supplies the Adreno GPU blobs needed for
the graphical login, modem firmware and domain maps, board-specific Wi-Fi
firmware, Bluetooth firmware, DSP and sensor firmware, TAS2557 speaker
firmware, and Venus video firmware. Without those inputs, display, radios,
audio, sensors, and hardware video cannot be expected to work. A usable public
firmware-free distribution would need a tested user-side assembly step that
adds firmware before the image is flashed, or a proven method to load it from
the user's existing device partitions. Neither method has been implemented or
validated for this release. A bare base image is at most a development artifact.

Public artifacts must contain no development-only sudo package, credentials,
or SSH host keys. Firmware package filtering alone is insufficient: inspect
files inside the rootfs.
The local `porthole build image` path currently uses pmbootstrap's configured
personal username and copies the developer's SSH public key into
`authorized_keys`. Its output is a development image. A public build needs a
reviewed generic account and must omit developer access keys; inspect the
assembled filesystem before promotion.
First-boot account access and recovery instructions must work on the image being
published. Never derive public flash slots from one maintainer's local handset.

## Manifest contract (schema 1)

A verified manifest contains `schema`, `id`, `device`, `channel`, `ui`, `init`,
`date` (ISO date), `status: verified`, and an HTTPS `build_url`. Names use lowercase
letters, digits, dots, underscores, plus or hyphens; no paths are accepted as IDs.

- `sources`: full commit IDs for `porthole`, `pmaports`, and `pmbootstrap`.
- `builder_sha256`: builder image digest without its `sha256:` prefix.
- `files`: objects with `name`, `size`, `sha256`, and `role` (`boot`, `rootfs`,
  and `dtbo` for devices that require it, or another supporting artifact). Add
  an immutable HTTPS `url` after publication.
- `packages`: exact dependency closure with `name`, `version`, and APK `sha256`.
- `indexes`: repository index `url` and `sha256` used for this build.
- `checks`: `boot-content`, `rootfs-packages`, `first-boot`, `firmware`, and
  `export` must each be true. Only the executed verifier may set these.

For a failed attempt, record the same identity/date/combination/build URL,
`status: failed`, and a useful `reason`; no download is advertised.

```sh
porthole release verify --manifest manifest.json --artifacts /path/to/files --json
```

This verifies the manifest contract and artifact hashes. It does not substitute
for the structural/rootfs checks that produced `checks`, nor verify a remote
signature. A future publisher must sign checksums, verify public downloads,
then propose the immutable manifest for review. Do not claim reproducibility
unless complete input retention and repeat builds demonstrate it.

## Hardware report

Record `schema: 1`, `device`, the rootfs `image_sha256`, `date`, `reviewer`, running
`kernel`, and `packages_sha256` of the installed package inventory. Each entry in
`tests` uses a configured capability name and `result`: `works`, `partial`,
`fails`, `untested`, or `not applicable`. Executed results also require `command`,
`control` (what proved execution), and a public HTTPS `evidence` URL.

Use `porthole matrix --json` for passive observations, and record functional
tests separately. Do not turn driver presence into a functional pass. Redact
captured evidence before publication; retain its relationship to the tested hash.

```sh
porthole release report --manifest manifest.json --report hardware.json --json
porthole release catalog --catalog releases --output site-src/devices --json
porthole docs build --catalog releases
```

The report validator rejects orphan/mismatched images and future dates. Freshness
is calculated using the profile's window; history is retained when stale. All
critical tests must pass on the exact candidate before promotion. Chromium needs
a real Wayland launch with no physical keyboard and normal browser sandboxing;
headless or compilation success alone cannot satisfy that test.

## Website and publication

The generated site separates the Porthole bring-up guide, the device directory,
and the downloads page. A device page shows no download until an immutable
verified manifest exists. For every artifact, publish its size and
SHA-256 beside the download. Verify downloaded bytes against that digest; sign
the manifest/checksum list and publish its detached signature with the image.
Keep build provenance and hardware reports linked to the exact image.

Upload complete immutable assets before updating the catalogue. Keep
publication credentials out of build jobs. Untrusted PRs never run on a
persistent release machine or receive the repository signing key.

## Retention

Keep three recent candidates and two promoted snapshots per enabled combination
initially, together with their package closure, manifests, and reports. Package
CI preserves old APK versions so cached indexes and retained snapshots continue
to resolve. Pruning requires explicit review of references; it is not part of a
normal publish. Never overwrite a package version with different bytes.

On GitHub, each release asset must be under 2 GiB. Use object storage for larger
images, and keep binaries out of Pages and Git history. A catalogue rollback
changes the advertised release; device rollback additionally requires validated
rootfs/user-data compatibility and the device's installation procedure.
