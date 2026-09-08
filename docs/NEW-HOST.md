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

## What `init` asks, and why each question exists

| question | writes | why it is asked |
|---|---|---|
| ssh username | `PORTHOLE_USER` | every tool talks to the device over ssh, never adb |
| the device's address | `PORTHOLE_HOST` | see [The address](#the-address) — there are two right answers |
| where builds run | nothing, or the host-tier keys | the one decision that changes what this machine needs |
| pmaports | `PORTHOLE_PMAPORTS_<DEVICE>` | where your device's kernel aport and device package live |
| the working repo | `PORTHOLE_WORKDIR_<DEVICE>` | see [The working repo](#the-working-repo) |

## The address

There are two ways to reach a postmarketOS device and porthole uses **ssh on
both** — never adb.

**Over USB — `172.16.42.1`.** postmarketOS brings a USB network gadget up in
its initramfs, so this address is the same on every device it supports and it
works before wifi is configured. It is the default because it is the answer
that is right on a device nobody has set up yet. `init` checks whether an
interface on this host currently holds an address on `172.16.42.0/24`, which
tells you whether the cable is in and the device has booted far enough to
enumerate.

**Over wifi — whatever your router gave it.** Faster, and it survives
unplugging the cable. It also *changes*, so it is not something to write down
once: read it off the device with `ip -4 -br addr` and re-run `porthole init`,
or set `PORTHOLE_HOST` for a single command.

Whatever you answer, `init` pings it and says whether it replied. A quiet
address is not an error — the device is usually off during setup — it is
information you would otherwise get four commands later.

> `porthole doctor` catches the two ways this goes wrong afterwards: a legacy
> `HOST` or `PHONE` export outranking `PORTHOLE_HOST` and talking to the old
> address in silence, and the USB gadget taking over the host's default route
> (it is a DHCP server with no upstream, so the *host* loses internet whenever
> the phone enumerates).

## The working repo

Every device gets one directory of its own: your notes, your logs, your
`docs/`, and `linux/` if you build a kernel from a tree. porthole writes
nothing into it uninvited; it is yours, and it is usually a git repo.

Without it, `porthole build`, `porthole verify`, `porthole dts` and six of the
milestones `porthole next` reports have nothing to read, and all of them say
so in terms of a variable rather than in terms of the thing that is missing.

`init` looks for one named after the device — `taimen` for `google-taimen` —
beside the porthole checkout and under the usual roots in `$HOME`, offers what
it finds, and offers to create one beside porthole if it finds nothing. It
never picks between two candidates on its own.

The key is **`PORTHOLE_WORKDIR_<CODENAME>`**, not the bare `PORTHOLE_WORKDIR`.
A working repo belongs to one device, and the bare key is ignored outright the
moment a second device declares its own — so a value written there on a
two-device host is present, looks right, and is not used.

```sh
porthole use google-taimen --workdir ~/ws/pmos/taimen   # or set it later
cd "$(porthole cd)"                                     # go there
```

### The workspace has to be restarted after you set it

Container mounts are fixed when the container is created. A workspace started
before the working repo was configured has no `/work` in it, so no kernel or
image build can run there. `porthole sandbox status` reports it, `porthole
build` routes around it and says why, and `porthole sandbox up` refuses to
pretend:

```sh
porthole sandbox down && porthole sandbox up
```

## Your first build needs no kernel tree

The rung ladder that `porthole build` prints is about kernel work, and every
rung on it compiles a tree — except one.

```sh
porthole sandbox up
export TK_PMOS_PASSWORD=...        # the rootfs user's password
porthole build image                        # preview: where it runs, what is missing
porthole build image --yes                  # pmbootstrap install + export, ~20m
porthole flash full --yes --replace-rootfs  # rootfs AND boot
```

`image` builds the whole system from pmaports as it stands. The kernel comes
from the aport, which is also what gives the exported `boot.img` something to
be verified against — so `porthole flash` accepts an image built this way,
which it cannot do for an export with no reference at all.

### What the workspace can and cannot produce

A rootless container cannot attach a loop device, and `pmbootstrap install`
uses one to build the rootfs **disk image**. So in the workspace the rung
passes `--no-image` and says so: you get a populated rootfs chroot and a
complete, verified `boot.img`, and no rootfs image.

```sh
porthole run tools/ph-flash-boot.sh    # in the workspace: boot only
porthole build image --yes --host      # if you need the rootfs image too
```

When you do have a rootfs image, flash **both** it and boot: `pmbootstrap
install` runs `mkfs` and remints the filesystem UUIDs, so a boot image flashed
on its own names a root that no longer exists and the initramfs hunts for it
forever. `porthole flash` refuses a rootfs image that is meaningfully older
than `boot.img` for exactly that reason — a run that dies partway leaves a
`truncate`d file where `flash_rootfs` looks, and nothing else can tell it from
a real one.

`porthole build` with no arguments previews rather than builds. It prints
where the build would run (workspace or host), which work dir it would use,
whether there is a kernel tree, and dims the rungs that need one.

## The compiler cache

It is the single biggest lever on build time and it was invisible.

```sh
porthole build ccache             # size, ceiling, hit rate, per arch
porthole build ccache --max 25G   # raise the ceiling
```

A first build of anything is all misses — that is the cache filling, not
failing. What costs you is the **ceiling**: ccache's own default is 5G, one
kernel build puts ~0.4G in, and past the ceiling ccache evicts, which turns
the next rebuild back into a full build without saying so. The setting is
written into the cache directory itself, so it survives a chroot being
recreated.

Measured on the reference host: a forced kernel rebuild with a warm cache took
**2m33s** against ~12 minutes cold.

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
| `PORTHOLE_PMAPORTS_<DEVICE>` | `porthole init`, `porthole aports worktree` | both |
| `PORTHOLE_WORKDIR_<DEVICE>` | `porthole init`, `porthole use --workdir` | both |
| `TK_PMOS_PASSWORD` | you, in your shell — never a file | both |

`PORTHOLE_PMB_DIR` and `PORTHOLE_SANDBOX_PMB_DIR` are **different
directories on purpose** and are not interchangeable. A rootless container maps
your uid and nothing else, so a work dir created by host root reads as `nobody`
inside it and cannot be written — nor chowned, because its files belong to uids
the namespace cannot see. The container has to own its own.

## pmaports

`porthole init` offers what it can already find, and otherwise asks:

1. **use what is here** — writes nothing, because a redundant key pinned to
   pmbootstrap's own `cache_git` is a second place to be wrong the day
   pmbootstrap moves it
2. **a different checkout** — you give it a path, it writes
   `PORTHOLE_PMAPORTS_<CODENAME>`
3. **clone a fresh one** — into `~/.cache/porthole/aports/<codename>`

Whichever you pick, `porthole sandbox up` mounts that checkout into the
workspace at `/pmb/cache_git/pmaports` — the path pmbootstrap derives from its
work dir, so both pmbootstrap and `ph-build.sh` find it with no further
configuration. **You do not need to run `pmbootstrap init` on the host**; the
workspace tier exists so that you do not have to. Because container mounts are
fixed at creation, a workspace started before pmaports was configured cannot
see it — `porthole sandbox status` and `porthole sandbox up` both say so, and
`porthole sandbox down && porthole sandbox up` is the fix.

If you work on more than one device, `porthole aports worktree` is the next
step and is worth understanding. pmaports is ONE clone sitting on ONE branch,
and pmbootstrap writes to it, so two devices share it: building for one sees
whatever the other left checked out. A git worktree gives each device its own
working tree on its own branch, off the same object store — no second fetch,
no duplicated objects.

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
