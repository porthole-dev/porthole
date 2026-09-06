# Setting up a new development host

One command:

```sh
porthole init
```

Run it on a bare machine or a half-configured one. It never overwrites what is
already right — it reads what you have, uses it as the default for every
question, and reports each key as `kept`, `changed` or `added`. Running it
twice is safe, and the second run should say nothing changed.

Then:

```sh
porthole doctor      # what is still missing, and the command that fixes it
porthole next        # where this device is, and what to do next
```

That is the whole setup. The rest of this page is what those commands are
deciding, for when you need to know.

## The one decision: where builds run

There are two tiers, and picking one is the only choice that matters, because
it decides whether you need pmbootstrap on this machine at all.

| | **workspace** (default) | **host** |
|---|---|---|
| what runs the build | a rootless podman container | your machine |
| pmbootstrap CLI | in the image | you install it |
| `helpers/envkernel.sh` | in the image, pinned to the CLI | you clone the source |
| host prerequisites | podman | pmbootstrap, its chroots, its dependencies |
| privilege it holds | none | whatever pmbootstrap needs |
| work dir | `~/.local/var/porthole-sandbox` | `~/.local/var/pmbootstrap` |

**Take the workspace unless you have a reason not to.** The image installs the
pmbootstrap CLI *and* clones its source at the tag matching that CLI, so the
two cannot drift; a host install has to keep them in step by hand. Every
prerequisite below the podman line is one the workspace already carries.

`porthole build` routes itself: it uses the workspace when one is up, and the
host otherwise, printing which on every build. `--host` forces the host path.

### The thing that costs people an afternoon

**On the workspace tier you do not install pmbootstrap.** Not the CLI, not the
source. The image has both. If you installed pmbootstrap by hand before
reading this, nothing is broken — but it was not needed, and the host copy is
not what your builds are using.

## The keys, and which ones are yours to set

`porthole config` prints every resolved value and the layer it came from.
`porthole doctor` prints the resolved *path* for each of these and which key it
came from. Between the two you never have to guess which variable won.

| key | who sets it | tier |
|---|---|---|
| `PORTHOLE_PMB_DIR` | you, if the default disk is too small | host |
| `PORTHOLE_SANDBOX_PMB_DIR` | you, if the default disk is too small | workspace |
| `PORTHOLE_PMBOOTSTRAP_SRC` | `porthole init`, on the host tier | host |
| `PORTHOLE_PMAPORTS` | `porthole init` | both |
| `PORTHOLE_PMAPORTS_<DEVICE>` | `porthole aports worktree` | both |

`PORTHOLE_PMB_DIR` and `PORTHOLE_SANDBOX_PMB_DIR` are **different
directories on purpose** and are not interchangeable. A rootless container maps
your uid and nothing else, so a work dir created by host root reads as `nobody`
inside it and cannot be written — nor chowned, because its files belong to uids
the namespace cannot see. The container has to own its own.

## pmaports

`porthole init` asks one question and adopts whatever is already there:

1. **an existing checkout** — you give it a path, it writes `PORTHOLE_PMAPORTS`
2. **a fresh clone** — pmbootstrap's own, at `$PORTHOLE_PMB_DIR/cache_git/pmaports`
3. **this device's own worktree** — delegates to `porthole aports worktree`

The third is worth understanding if you work on more than one device. pmaports
is ONE clone sitting on ONE branch, and pmbootstrap writes to it, so two
devices share it: building for one sees whatever the other left checked out. A
git worktree gives each device its own working tree on its own branch, off the
same object store — no second fetch, no duplicated objects.

## Packages, per distribution

`porthole doctor` prints the right line for the host it is running on. These are
the same commands, collected:

```sh
# Debian / Ubuntu / Mint / Pop!_OS
sudo apt install python3 openssh-client android-sdk-platform-tools util-linux podman

# Arch / Manjaro
sudo pacman -S python openssh android-tools util-linux podman

# Fedora / RHEL
sudo dnf install python3 openssh-clients android-tools util-linux podman

# Alpine / postmarketOS
sudo apk add python3 openssh-client android-tools util-linux podman

# macOS -- ssh is preinstalled. pmbootstrap needs Linux, so builds happen
# elsewhere; probing and debugging a running device work fine.
brew install python android-platform-tools flock
```

On an rpm-ostree host (Silverblue, Kinoite) there is no `dnf`. `doctor` detects
that and suggests unpacking Google's platform-tools into `~/.local/bin` rather
than a layered package that needs a reboot.

## USB access without root

On Linux, fastboot needs a udev rule or it only works under `sudo`:

```sh
sudo tee /etc/udev/rules.d/51-android.rules >/dev/null <<'RULE'
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0666", GROUP="plugdev"
RULE
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG plugdev "$USER"     # log out and back in
```

`18d1` is Google. Substitute your vendor's ID — `lsusb` while the device is in
the bootloader will show it.

## The one step that still needs root, once

Installing podman, and registering binfmt for cross-architecture builds. Both
are host-global and both are a person installing software on their own
computer. `porthole doctor` names them and prints the command; it does not run
them.

## Moving from another host

`porthole config --json` on the old machine is the diff. Copy nothing by hand:
`config.env` holds paths that are only true on the machine that wrote it. Run
`porthole init` on the new one and let it find the local answers.
