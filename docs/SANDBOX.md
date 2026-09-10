# Running pmbootstrap without handing over the host

> Setting a machine up? Start at [Setting up a new host](NEW-HOST.md) -- one command, and it decides
> most of what is below for you. This page is *why* the workspace exists, not how to get one.

## The problem

pmbootstrap needs root. It bind-mounts, unmounts, chroots, creates device
nodes, and writes into chroots — none of which an unprivileged user can do.

The usual way to make that bearable in an agentic loop is:

```
Defaults:you timestamp_timeout=9999
```

That is a **167-hour root credential cache**. Type your password once and, for a
week, *every* process running as you gets silent, unlimited root — an agent, a
build script, a compromised npm or pip dependency, a prompt injection arriving
through a log file it read. There is no allowlist, no audit trail, and no
distinction between "pmbootstrap needs to bind-mount a chroot" and
"something just rewrote `/etc/sudoers`".

It is not that agents are untrustworthy in particular. It is that the blast
radius of any mistake, from any source, is the whole machine.

## The answer: don't have root at all

```sh
porthole sandbox up                          # build the image, start the workspace
porthole sandbox shell --command <command>   # run one command in it
porthole sandbox shell                       # a shell, for a human
porthole sandbox status                      # what is up, what is missing
```

The workspace keeps its **own pmbootstrap work directory**, at
`~/.local/var/porthole-sandbox` (override with `PORTHOLE_SANDBOX_PMB_DIR`).
That is forced, not chosen: `--userns=keep-id:uid=0,gid=0` maps your uid and
nothing else, so a work dir created by host root reads as `nobody` inside and
cannot be written -- nor chowned, because its files belong to uids the
namespace cannot see. The container has to create its own. Your host work dir
is untouched, and `porthole build --host` still uses it.

The same rule applies to a kernel tree's `.output`: it belongs to one
environment. Building the same tree in the other refuses and names the ways
out, rather than failing inside kbuild.

```sh
porthole sandbox down                        # stop it; your files are untouched
```

A **persistent, named, rootless container**. The whole trick is one flag:

```sh
podman run --userns=keep-id:uid=0,gid=0 ...
```

Inside, you are root — so pmbootstrap uses **no sudo at all**, because
`which_sudo()` returns `None` when `os.getuid() == 0` (`pmb/config/sudo.py`).
Outside, that root is your own unprivileged uid. An escape gets your uid, not
the machine.

**The default install grants no sudoers entry, and no standing privilege of any
kind.** That is the point: there is nothing for a mistake, a dependency or an
injection to spend.

### Why persistent

Not `--rm`. All state lives in the mounts below, so the container itself is
disposable and recreating it costs nothing — while recreating it per command
would cost a second every time and lose the running adb server. It also means a
human can open it in Ptyxis, or `podman exec -it porthole-sandbox bash`, and
work in the same environment the agent drives. One environment, two front
doors.

### `--command` is what makes it usable by an agent

`_shell` once passed `-it` unconditionally. An agent has no TTY, so podman
failed before the command started, and the container tier had never once been
reachable from the thing it was built for. `-it` is now used only when stdin is
a terminal *and* no `--command` was given.

### What it can reach

Only what it mounts, and that list **is** the isolation boundary:

| mount | why |
|---|---|
| the pmbootstrap work directory | the chroots and package cache |
| this repo | the toolbox itself |
| `/dev/bus/usb` | flashing; the host ACL grants your uid, and `keep-id` carries it in |
| the device-mutex lock file | so a containerised agent and a host agent share one lock |
| `~/.config/porthole`, **read-only** | so both sides resolve the *same* device |
| a dedicated ssh key, **read-only** | so the workspace never needs `~/.ssh` |

Your `~/.ssh`, `/etc`, home directory and other users' data are not present.

Two of those are subtler than they look, and both were found in review rather
than designed in:

- The **config mount is read-only** because `config.env` sets `FASTBOOT` and
  `ADB`, and the *host* executes those values as commands. Writable, it would
  have been a container-to-host code execution channel. The cost is that
  `porthole use` does not work from inside the container; switch devices on the
  host.
- The **device lock** needs host and container to compute the same path. That
  needs the config mount *and* `TK_DEVICE_LOCK` passed through *and* the lock
  recorded as a container label, so that a later `porthole use` cannot leave
  the container guarding a different phone while the mutex still looks healthy.

## What it does not do

Being straight about this matters more than the feature list.

**It does not protect the device.** A phone you have given passwordless sudo to
is a phone a tool can brick. The device mutex, the forbidden-slot guard and
confirm-before-irreversible are what cover that.

**One host step still needs root, once.** Installing podman, and registering
binfmt for cross-architecture builds, are host-global. That is a person
installing software on their own computer — not a privilege the agent holds,
and not something it can do. `porthole doctor` names both.

**Loop devices are unavailable to a rootless container.** That is a kernel
boundary, not a configuration knob: `LOOP_SET_FD` and block-device `mount`
require `CAP_SYS_ADMIN` in the *initial* user namespace, and `/dev/loop-control`
is `root:disk` besides. Adding yourself to `disk` would work and is a worse
standing privilege than the sudoers entry this tier exists to avoid — `disk` is
raw read-write on every block device on the machine.

It matters less than it sounds. `pmbootstrap install --no-image` never touches
a loop device (`_install.py` returns before `install_system_image`), so the
install rungs pass it whenever no loop device is present and everything else
still runs: the rootfs chroot is populated and `pmbootstrap export` packs
`boot.img` from it. Porthole assembles the rootfs disk image itself
afterward — `_ph_assemble_image` (tools/ph-build.sh) builds it straight from
that chroot with `mkfs.ext4 -d` (which populates a filesystem from a
directory, no mount involved) and `sfdisk` (which partitions a plain file),
neither of which needs a loop device or `CAP_SYS_ADMIN`. `fuse2fs` was never a
way around the loop device for this — it mounts a filesystem, and what
pmbootstrap wanted a loop device for was a **partitioned disk**; the assembler
sidesteps the boundary instead of working around it. See
`brain/findings/fuse2fs-cannot-replace-the-loop-device.md` and
`docs/SANDBOX-PROVISIONING.md` for the history.

**The workspace produces the rootfs disk image too, now.** `porthole flash
full --yes --replace-rootfs` flashes both the rootfs image the workspace
assembled and the `boot.img` it packed — no `--host` step is needed to get a
rootfs image at all. `--host` remains a choice of build site (bare-metal
pmbootstrap instead of the sandbox), not a requirement for producing one.

## There is no second, weaker path

A validating privilege broker (`sandbox/ph-sudo`) used to live here for a host
that cannot run podman: 26 allowed verbs, every path confined to declared
roots, every decision audited. **It has been removed.**

It was a good broker and that was not enough. It granted a real
`NOPASSWD` sudoers entry, and this document said plainly that it could not
contain a determined chroot payload — pmbootstrap legitimately needs to write
executables into a chroot and then run them, so any allowlist that lets
pmbootstrap work also lets a payload inside that chroot work. The workspace
needs **no sudoers entry at all**, which is a boundary of a different kind
rather than a better allowlist.

Keeping both meant every reader — and every agent — had to work out which tier
they were on, and the weaker one was the one a stuck agent would reach for. A
fallback that still exists is a fallback something can be talked into using.
Podman is now the single host prerequisite; `porthole doctor` names how to
install it.

`PMB_SUDO` went with it. Nothing here reads it, and `porthole doctor` fails if
it is still exported: pmbootstrap invokes it directly, so a leftover value
kills a build with exit 78 from deep inside pmbootstrap, naming nothing.

`tests/test_sandbox_container.py` covers the workspace, and its assertions are
the same idea: that the mount set never exposes `~/.ssh`, that the device key
is read-only, that the lock path matches `tools/ph-device.sh` exactly, that
`--command` omits `-it`, that `down` never passes `-v`, and that a failed
`podman inspect` refuses rather than reading as "no drift".

## If you only do one thing

Delete the `timestamp_timeout` line. Even with no workspace at all, going back
to a normal 5-minute sudo cache shrinks a week-long window to a few minutes,
and every escalation after that is one you were present for. `porthole sandbox
status` reports it, and on the reference host that check had itself been dead:
sudoers joins options with commas, the parser split on whitespace, and a
167-hour cache read as clean.

## Building and testing an upstream project in here

The image carries `meson`, `ninja`, `py3-pytest` and `py3-dbusmock`, so an
upstream tarball unpacked under `/work` can be configured, built and have its
own test suite run without installing a toolchain on the host:

```sh
porthole sandbox shell --command \
  "sh -lc 'cd /work/xdg-desktop-portal-src && meson setup build && meson test -C build -v'"
```

A project's **own** build dependencies are deliberately not in the image —
xdg-desktop-portal alone wants `flatpak-dev`, `pipewire-dev`,
`gst-plugins-base-dev`, `geoclue-dev` and `fuse3-dev`, which is a lot of image
for one project. Install them on demand from the aport that already lists them,
which is also the list that is guaranteed to be right:

```sh
porthole sandbox shell --command \
  "sh -lc 'apk add \$(. /pmb/cache_git/pmaports/temp/xdg-desktop-portal/APKBUILD; echo \$makedepends)'"
```

The container is persistent, so that install survives until the image is
rebuilt (`porthole sandbox build --force`), at which point re-run it.

This exists because on 2026-09-10 the NFC portal's pytest suite had nowhere to
run: neither the host nor this image had meson, and the work had to go to a
distrobox. Compiling a package through `porthole pkg build` is not the same
thing — that runs abuild in pmbootstrap's chroot and never runs the project's
own tests.
