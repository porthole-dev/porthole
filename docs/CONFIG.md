# Configuration reference

Two files, two questions.

| question | file | committed? |
|---|---|---|
| who am I, where is my tooling | `~/.config/porthole/config.env` | **no** |
| which device is this | `profiles/<codename>/device.env` | yes |

A developer with two phones swaps `PORTHOLE_DEVICE` and changes nothing else. A
second developer on the same phone copies neither — they write their own
`config.env` against the same committed profile.

## Resolution order

Lowest precedence to highest:

1. built-in defaults in `lib/porthole.{sh,py}`
2. `profiles/$PORTHOLE_DEVICE/device.env`
3. `${XDG_CONFIG_HOME:-~/.config}/porthole/config.env`
4. `$PORTHOLE_ROOT/.env` — per-checkout override, gitignored
5. the process environment — always wins

`porthole config` prints every resolved value with the layer it came from.

## Format

`KEY=value`, one per line, `#` comments. A leading `export` and spaces around
`=` are tolerated. A quoted value ends at its closing quote, so
`KEY="a"  # why` yields `a`.

Not YAML or TOML on purpose: shell parses it without a dependency and python
parses it in ten lines, and the toolbox is half of each.

## Identity keys

| key | meaning | default |
|---|---|---|
| `PORTHOLE_DEVICE` | which profile to load | *(none)* |
| `PORTHOLE_USER` | ssh username on the device | `user` |
| `PORTHOLE_HOST` | device IP | `172.16.42.1` |
| `PORTHOLE_SSH_PORT` | ssh port | `22` |
| `PORTHOLE_SSH_KEY` | identity file | *(none)* |
| `PORTHOLE_AGENT` | default `TK_AGENT` for the mutex | *(none)* |
| `FASTBOOT`, `ADB` | tool paths | found on `$PATH` |
| `PORTHOLE_PMB_DIR` | pmbootstrap work dir, **host builds** | `~/.local/var/pmbootstrap` |
| `PORTHOLE_SANDBOX_PMB_DIR` | pmbootstrap work dir, **workspace builds** | `~/.local/var/porthole-sandbox` |
| `PORTHOLE_PMBOOTSTRAP_SRC` | pmbootstrap checkout, for `helpers/envkernel.sh` (host builds only) | *(none)* |
| `PORTHOLE_PMAPORTS` | pmaports checkout | pmbootstrap's `cache_git/pmaports` |
| `PORTHOLE_PMAPORTS_<DEVICE>` | pmaports checkout for one device; beats the global | *(none)* |
| `PORTHOLE_PMAPORTS_FORK_URL` | profile key: what `porthole init` clones for this device | vanilla `postmarketOS/pmaports` |
| `PORTHOLE_WORKDIR` | the device working repo (kernel, pmaports, blobs) | *(none)* |

The two work dirs are **different directories and not interchangeable**. A
rootless container maps your uid and nothing else, so a work dir made by host
root reads as `nobody` inside it and can be neither written nor chowned. Which
one a build used is printed by that build, and by `porthole doctor`.

You should not need to set any of these by hand. `porthole init` resolves them,
and `porthole doctor` prints the resolved path for each one *and the key it
came from* -- which is the question these rows otherwise leave you to answer by
elimination. See [Setting up a new host](NEW-HOST.md).

## Behaviour keys

| key | meaning | default |
|---|---|---|
| `PORTHOLE_POLL` | poll interval, seconds | `0.5` |
| `PORTHOLE_CONNECT_TIMEOUT` | ssh connect timeout | `2` |
| `PORTHOLE_NO_MUX` | `1` disables ssh multiplexing | `0` |
| `PORTHOLE_MUX_PERSIST` | `ControlPersist` value | `60s` |
| `PORTHOLE_TIMING` | `1` prints per-probe timings to stderr | `0` |
| `PORTHOLE_RUNDIR` | ssh control sockets, locks | `$PORTHOLE_ROOT/.run` |

## Device keys

See `profiles/_template/device.env` — every key is listed there with what it
means and, for the ones marked `(TRAP)`, why getting it wrong produces a
confident wrong answer rather than an error.

`PORTHOLE_ARCH_DIR` is derived, not set: `aarch64` → `arm64`, because a package
says one and a kernel tree says the other.

## pmaports fork

`PORTHOLE_KERNEL_PKG` names an aport, but that aport is not necessarily in
upstream postmarketOS pmaports at all -- most bring-ups are not, and a device
archived there keeps the archived name, not the one the port actually builds
(google-taimen's msm8998 kernel is `linux-postmarketos-qcom-msm8998-7.2`;
upstream's archived one is the unversioned `linux-postmarketos-qcom-msm8998`,
years behind).

`porthole init`'s "clone a fresh one" answer for pmaports clones
`PORTHOLE_PMAPORTS_FORK_URL` from the device's profile when it is set, and
vanilla `https://gitlab.postmarketos.org/postmarketOS/pmaports.git` otherwise.
Leave it empty only when the device's aports really are upstream; getting this
wrong is silent until the first build, which fails at the very first step
with `could not read <pkg> pkgver/pkgrel` -- the aport the build wants simply
is not in the tree that was cloned.

## Package repository

A port that carries forks can publish them prebuilt, as a signed apk
repository, so that builds resolve them instead of compiling them and phones
upgrade from it. Three profile keys describe it:

| key | becomes | example (google-taimen) |
|---|---|---|
| `PORTHOLE_PKG_REPO_URL` | pmbootstrap `mirrors.pmaports_custom` | `https://github.com/porthole-dev/pmos-packages/releases/download` |
| `PORTHOLE_PKG_REPO_SYSTEMD_URL` | pmbootstrap `mirrors.systemd_custom`; empty for none | the same URL plus `/systemd` |
| `PORTHOLE_PKG_REPO_KEY` | a copy in `<work>/config_apk_keys/` | `keys/porthole-dev-packages-20260915.rsa.pub`, relative to the profile |

pmbootstrap asks for `<url>/<branch>/<arch>/APKINDEX.tar.gz`, where `<branch>`
is the channel's `branch_pmaports` (`main` on edge). A repository hosted as
GitHub releases therefore has one release per tree, tagged `main/aarch64`,
`main/x86_64`, `systemd/main/aarch64` and so on. The key file must keep the
name the index is signed with: apk finds the key by that name.

`PORTHOLE_PKG_REPO_URL=none` in `~/.config/porthole/config.env` turns it off on
one machine.

### In the workspace

`porthole sandbox up` probes the repository before it writes the pmbootstrap
config: it downloads the index for the device's arch **and the host's**,
anonymously, and checks the signature against the key. Only then does it set
the two mirrors and install the key, and it prints each change. If the probe
fails, the mirrors are left out and it says why; builds then use the
postmarketOS mirrors and build the forks from source. A configured repository
that pmbootstrap cannot fetch is worse than none, because pmbootstrap aborts
the whole command instead of falling back.

Why the host arch: pmbootstrap fetches every mirror's index for the host arch
when it creates the native chroot, and a 404 there aborts the build even
though nothing is installed from it. The repository needs a signed index for
`x86_64`, an empty one is enough. See
`brain/traps/a-package-mirror-needs-a-host-arch-index.md`.

Any other `[mirrors]` entry already in the workspace config is kept. A
different `pmaports_custom` or `systemd_custom` is replaced, and the output
names the old value.

`porthole doctor --all` repeats the probe and reports one of:

| verdict | meaning |
|---|---|
| `ok` | every index is there and verifies |
| `private` | every index answers 404. On GitHub this is a private repository: an anonymous download gets 404, not 403, and neither pmbootstrap nor apk can send a token |
| `host-arch-missing` | the target arch is published and the host arch is not |
| `missing` | the device's arch is not published |
| `key-mismatch` | signed by another key name, or the signature does not verify with the configured key |
| `unsigned`, `unreachable` | not an apk signature, or the network failed |

It fails when the workspace already uses a repository that no longer probes,
because that is the state that stops builds. Plain `porthole doctor` does not
use the network and reports the probe as skipped.

### On the host

`porthole build --host` uses your own pmbootstrap config. That config applies
to every device you build, so porthole does not edit it. Once the repository
probes `ok`, `porthole doctor --all` prints the commands:

```sh
pmbootstrap config mirrors.pmaports_custom <PORTHOLE_PKG_REPO_URL>
pmbootstrap config mirrors.systemd_custom <PORTHOLE_PKG_REPO_SYSTEMD_URL>
sudo install -m 644 profiles/<device>/<key> <PORTHOLE_PMB_DIR>/config_apk_keys/
```

The key needs `sudo` because pmbootstrap creates that directory as root on a
host install.

### On the phone

`pmbootstrap install` writes the configured mirrors into the image's
`/etc/apk/repositories`, and porthole's image assembly removes only the build
machine's local repository line. So an image built after the mirrors are set
lists the repository, and the device takes new builds with:

```sh
apk update && apk upgrade
```

The device verifies with `/etc/apk/keys/`, and porthole's image assembly
(`_ph_assemble_image` in `tools/ph-build.sh`) copies the work dir's
`config_apk_keys/*.pub` there, the key included. Without the key, apk reports
the repository as `UNTRUSTED signature` and installs nothing from it. On a
phone flashed before the repository existed, add it by hand (the lines are the
mirror URLs plus the branch):

```sh
printf '%s\n' <PORTHOLE_PKG_REPO_URL>/main <PORTHOLE_PKG_REPO_SYSTEMD_URL>/main \
    | sudo tee -a /etc/apk/repositories
sudo cp porthole-dev-packages-20260915.rsa.pub /etc/apk/keys/
sudo apk update
```

## Legacy names

These are honoured and **win** over their porthole-namespaced twins, so every
command line in older documentation keeps working:

| legacy | behaviour |
|---|---|
| `PHONE` | verbatim if set; else `$PORTHOLE_USER@$HOST` |
| `HOST` | verbatim if set; else `TK_HOST`; else the host part of `PHONE`; else `PORTHOLE_HOST` |
| `TK_HOST` | as above |
| `FASTBOOT` | unchanged |
| `TK_POLL` | beats `PORTHOLE_POLL` |
| `TK_AGENT` | beats `PORTHOLE_AGENT` |
| `TK_FORCE` | beats `PORTHOLE_FORCE` |
| `TK_DEVICE_LOCK` | beats `PORTHOLE_DEVICE_LOCK` |
| `TK_DEVICE_STATE` | beats `PORTHOLE_DEVICE_STATE` |
| `TK_DEVICE_TIMEOUT`/`_MAX` | unchanged |
| `TK_PMOS_PASSWORD` | beats `PORTHOLE_PMOS_PASSWORD` |
| `TK_LOGIN_PASSWORD` | beats `PORTHOLE_LOGIN_PASSWORD` |
| `TK_RUN_TIMEOUT` | beats `PORTHOLE_RUN_TIMEOUT` |
| `TK_BOOT_DEADLINE` | beats `PORTHOLE_BOOT_DEADLINE` |
| `TK_SCROLL_URL` | beats `PORTHOLE_SCROLL_URL` |
| `TK_WKPHASE_OFFSETS` | beats `PORTHOLE_WKPHASE_OFFSETS` |
| `TK_SSH_OPTS` | the ssh option array itself, not a knob — see below |
| `tools/ph-lib.sh` | still sourceable — a symlink to `lib/porthole.sh` |

Every row is asserted in `tests/test_config.py` and `tests/test_shell_lib.sh`,
so a future refactor cannot quietly break one, and
`tests/test_conventions.py::test_no_new_environment_knob_carries_the_old_prefix`
holds the list closed: a name that is neither on it nor `PORTHOLE_*` fails.

`TK_SSH_OPTS` is on that list but is not a knob and has no twin. `lib/porthole.sh`
**builds** it out of `PORTHOLE_CONNECT_TIMEOUT`, `PORTHOLE_SSH_PORT` and
`PORTHOLE_SSH_KEY`; a `PORTHOLE_SSH_OPTS` input would be a second authority
over the same array rather than another name for it. Set the three that feed it.

### Everything else moved outright

The other 71 `TK_*` names were each read by a single tool, so an alias for a
name nothing else says would be dead weight. They are now `PORTHOLE_*` with
the same suffix — `TK_CAP_PORT` is `PORTHOLE_CAP_PORT`, `TK_DTC_OUT` is
`PORTHOLE_DTC_OUT` — and each tool's `env:` header names the new one. **These
have no alias**: a command line that exports one of them by its old name will
run with the tool's default instead, silently. `porthole tools <name>` prints
the header if you need to check one.
