# Sandbox provisioning — design

<!-- porthole:design-doc -- phases 0,1,2,3,4 are BUILT -->

**Status:** phases 0-4 have all shipped. Phase 3's image build -- §4 below,
whose original fuse2fs-shim design was wrong -- landed as
`_ph_assemble_image` (tools/ph-build.sh): `mkfs.ext4 -d` and `sfdisk` against a
plain file, UUIDs chosen up front rather than read back, exactly the
resolution §4's "now the open decision" paragraph anticipated. Verified on
hardware (Gate C5, 2026-09-08); see `docs/NEW-HOST.md` and `docs/SANDBOX.md`
for the operator-facing description. Supersedes the 2026-08-26 revision of
this file. Measurements re-run 2026-08-29.

## The goal, stated as a guarantee

**No standing host privilege. Not a sudoers entry, not a broker, not a rootful
container.** The default install grants zero, and an agent driving the full
bring-up loop — build, install, flash, test — holds nothing it could lose.

That is now achievable. The previous revision concluded it was not.

## What the previous revision got wrong

It measured that a rootless container cannot attach a loop device, and
concluded that `pmbootstrap install` — and therefore the whole `kernel` rung —
could not be containerised and had to stay on the host.

The measurement was right. The conclusion over-reached. Loop devices are not
load-bearing; they are only how pmbootstrap happens to build the image:

- `pmb/install/_install.py:1452` is `if no_image: return`, and it sits **before**
  `install_system_image()` — the only path that reaches
  `blockdevice.create()` → `losetup.mount()`. So `pmbootstrap install
  --no-image` never touches a loop device at all.
- The loop device exists only so that `mount` has a block device to point at.
  `fuse2fs` mounts ext4 from a **plain file**, and FUSE is `FS_USERNS_MOUNT`, so
  it is legal inside a rootless user namespace where ext4-on-loop is not.

The rung did not need real root. It needed to stop using loop devices.

There is a second, more general lesson, and it is the same one the broker
taught: **a denial is information.** The 2026-08-26 probe stopped at "denied"
and designed around it. The right next question was "what is the denial
actually protecting, and does the work need that thing at all?"

## What was measured, 2026-08-29

Fedora Silverblue 44 (ostree, read-only `/`), podman 5.8.4 rootless, kernel
7.1.10, Pixel 2 XL attached. **Re-run these before trusting them elsewhere.**

| probe | result |
|---|---|
| `fuse2fs` mounts ext4 from a plain file, rootless | **works** |
| ...`chown 0:0` inside it, with `-o fakeroot` | works |
| ...contents survive unmount and remount | works |
| ...`mknod` inside the FUSE mount | **denied** — see below, it does not matter |
| ...journal writes | unsupported (warning only; data persisted) |
| `/dev/bus/usb` bind mount type | `devtmpfs (rw,seclabel,nosuid)` — **not** `nodev` |
| `O_RDWR` on the device node, in-container | **works** — host ACL `user:<you>:rw-` applies through `--userns=keep-id` |
| a **new** USB node, created after container start | **visible and usable** |
| `fastboot devices` from that already-running container | **works** — `<taimen-serial> fastboot` |
| reaching the phone at `172.16.42.1` on the **default** netns | **works** — rootless podman 5.8.4 uses `pasta`, which forwards outbound via the host |
| binfmt `qemu-aarch64` | registered host-side, inherited |

### 4c. What the workspace turned out to need, measured 2026-08-29

The image ships four shims, and each one exists because a rootless user
namespace cannot do something pmbootstrap assumes. None of them grants
privilege; each removes an assumption that only holds for real root.
`brain/findings/what-a-rootless-workspace-cannot-do.md` carries the probes.

| shim | why |
|---|---|
| `pmbootstrap` | refuses uid 0 outright, before reading its config. `--as-root` is the only way past, and the source checkout's entry point needs the same treatment because envkernel calls it by absolute path |
| `mknod` | device-node creation is denied in a userns whatever CapEff says. Gives the chroot a **recursive** bind of the container's `/dev` instead -- per-node binds pass every check and then fail `open(O_CREAT)`, which is every shell redirect |
| `chmod` | pmbootstrap chmods each node it makes; a bound node belongs to the host's `/dev` and cannot be chmodded even to the mode it already has |
| `sudo` | absent, and envkernel calls it for its bind mount. Runs the command as who we already are, and refuses if that is not root |

**The workspace also gets its own pmbootstrap work directory.** A work dir
built by host root is unusable and unconvertible in a rootless namespace, and
so is a kernel tree's `.output`. That is not a shortcoming of the mounts; it is
what `--userns=keep-id:uid=0,gid=0` means. `porthole sandbox up` creates and
configures the work dir; a tree's `.output` belongs to one environment and the
build says so rather than failing inside kbuild.

### The `mknod` denial does not matter

Unprivileged FUSE mounts are forced `nodev`, so no device node can be created
inside a fuse2fs-mounted image. That would be fatal if the image needed device
nodes. It does not: `_install.py:158` fills the image with `cp -a` from the
device rootfs chroot, and that chroot contains **zero** device nodes
(`find chroot_rootfs_* \( -type c -o -type b \)` → 0). pmOS gets `/dev` from
devtmpfs at boot.

This is worth re-checking if the install flow ever changes, and it is a test
below rather than a note.

### The re-enumeration probe, and why it was the one that mattered

Flashing depends on a node that does not exist when the container starts: the
phone drops to the bootloader and re-enumerates as a *different* device number.
Reasoning said it would work (a live devtmpfs bind, plus a host-side `uaccess`
ACL applied to every new node). Reasoning is not evidence.

Run for real: container started while the phone was booted (node `003`,
`18d1:d001`), then `tools/ph-to-fastboot.sh` under the device mutex. The old
node vanished, node `004` appeared as `18d1:4ee0`, and the **already-running**
container ran `fastboot devices` and `fastboot getvar current-slot` against it
successfully.

Reproduce:

```sh
podman run -d --name probe --userns=keep-id:uid=0,gid=0 \
  --security-opt label=disable -v /dev/bus/usb:/dev/bus/usb alpine:3.22 sleep 3600
podman exec probe apk add -q android-tools
TK_AGENT=you tools/ph-device.sh --need-booted tools/ph-to-fastboot.sh
podman exec probe fastboot devices        # must print the serial
```

Incidentally this re-confirmed the profile's own trap: `lsusb` reports the
**booted** gadget as `18d1:d001`, which reads as fastboot and is not. Only
`fastboot devices` discriminates. An empty `fastboot devices` on a booted
device is the correct answer, not a fault.

### Space, measured

| | |
|---|---|
| `chroot_native` | 15 G |
| `cache_apk_aarch64` | 2.0 G |
| `chroot_rootfs_google-taimen` | 2.1 G |
| workdir total | **29 G** |
| the disk it sits on | 382 G, **87 % used, 50 G free** |

Space is a live constraint on the reference host, not a hypothetical. A kernel
tree and its objects are on top of the 29 G above.

## Design

### 1. The workspace is a persistent container, not a build jail

A **named, long-lived** rootless container — `porthole-sandbox`, tagged to
`VERSION`. Not `--rm`.

```sh
podman run -d --name porthole-sandbox \
  --userns=keep-id:uid=0,gid=0 \
  --cap-add SYS_ADMIN,SYS_CHROOT,MKNOD \
  --device /dev/fuse \
  --security-opt label=disable \
  --hostname porthole-sandbox \
  -e XDG_CONFIG_HOME=/run/porthole/config \
  -e TK_DEVICE_LOCK=<lock> \
  -e PORTHOLE_SSH_KEY=/run/porthole/device_key \
  --label io.porthole.device-lock=<lock> \
  -v <workdir>:/pmb:rw \
  -v <repo>:/porthole:rw \
  -v /dev/bus/usb:/dev/bus/usb:rw \
  -v <lock>:<lock>:rw \
  -v ~/.porthole/device_key:/run/porthole/device_key:ro \
  -v <PORTHOLE_WORKDIR>:/work:rw \
  -v ~/.config/porthole:/run/porthole/config/porthole:ro \
  porthole-sandbox:<VERSION> sleep infinity
```

That block is the isolation boundary, so it is written out in full: anything
not named here is not reachable from inside. `<lock>` is
`/tmp/porthole-<device>.lock`, the same path `tools/ph-device.sh` computes, and
the `--label` records it so `up` and `shell` can refuse a container still
guarding the device that was active when it was created. The `/work` and
config mounts appear only when they exist; `--mount` appends more under
`/mnt/`.

The config mount is **read-only on purpose**. It is there so the container
resolves the same `PORTHOLE_DEVICE` as the host — which needs reads. Writable,
`config.env` would be a container-to-host code execution channel: it sets
`FASTBOOT` and `ADB`, and the *host* executes those values as commands.

`--userns=keep-id:uid=0,gid=0` is the whole trick: inside you are root, so
`which_sudo()` returns `None` (`pmb/config/sudo.py:17`) and pmbootstrap uses no
sudo at all; outside, that root is your own unprivileged uid.

All state lives in mounted volumes, so recreating the container on an image
bump costs nothing. That is the distrobox model, and it is why persistence is
safe rather than a source of drift.

Two front doors, one environment: an agent drives it with `podman exec`; a
human opens it in Ptyxis (which lists podman containers) or with
`podman exec -it porthole-sandbox bash`. **distrobox is deliberately not a
dependency** — what it would buy is "a named container with a shell", which
podman already provides, and its default whole-`$HOME` mount would delete the
isolation this design exists for.

adb, fastboot, pmbootstrap, envkernel and the toolchain live **only** in the
image.

### 2. The device link, and the ssh key problem

The primary link to a booted device is **ssh over a USB network gadget**
(`<user>@172.16.42.1`), not adb. Two consequences:

- **No network flag is needed.** pasta forwards outbound traffic through the
  host, so the default netns reaches the phone. `--network=host` would work and
  is not used, because it needlessly gives up isolation.
- **The container needs an ssh identity**, and today `PORTHOLE_SSH_KEY` is
  unset, so ssh falls back to `~/.ssh/id_ed25519` — a personal key. Mounting
  `~/.ssh` would break the exact promise `docs/SANDBOX.md` makes.

So: `porthole sandbox up` generates a **dedicated device key** at
`~/.porthole/device_key`, points the already-existing `PORTHOLE_SSH_KEY` knob
at it (`/run/porthole/device_key` inside the container — deliberately outside
the `/porthole` repo mount), and mounts only that one file read-only. Installing its public half on
the device is a one-time step over the existing connection.

No new mechanism — the config knob already exists and is simply unused.

### 3. `sandbox/Containerfile`

Alpine **3.24**, **pinned** (not `latest` — a sandbox that changes under you is
not a sandbox), plus: `pmbootstrap`, `android-tools`, `e2fsprogs`,
`fuse2fs` (its own aport — **not** part of `e2fsprogs-extra`, verified
2026-08-29), `fuse`, `git`, `python3`,
`openssh-client`, `rsync`, `openssl`, `xz`, `tar`, `util-linux`, and the
build deps envkernel expects.

### 4. The image build — NOT a fuse2fs shim

**This section originally described a shim and was wrong.** See
`brain/findings/fuse2fs-cannot-replace-the-loop-device.md`.

`fuse2fs` mounts a filesystem. pmbootstrap is not mounting a filesystem from a
file — it is using the loop device to expose a **partitioned disk** so the
kernel creates `/dev/installp1` and `p2`, which `parted`, `mkfs` and `mount`
all then consume as block devices (`partition.py:58-61`, `format.py:260-266`).
There is no point in that chain where `fuse2fs` has anything to substitute for,
and `--split` still calls `losetup` per image.

What survives is the opening, not the mechanism:

- `pmbootstrap install --no-image` never reaches any of it (source-verified).
- `mkfs.ext4` needs no block device, and `mkfs.ext4 -d <dir>` populates a
  filesystem from a directory with **no mount at all**.

So the image can be assembled unprivileged — per-partition filesystem images,
then `truncate` + `sfdisk` + `dd` on a plain file — but porthole would then own
the partition layout and the filesystem UUIDs that `boot.img` hard-codes.
`ph-build.sh:510` records a real device failure from exactly that pair
disagreeing. **That trade-off is now the open decision**, and it is a different
decision than the one this section originally recorded as settled.

### 4b. Where the source lives

**The kernel tree stays on the host and is bind-mounted at `/work`.** It is not
copied into the image and not moved. A bind mount is native speed, and
`--userns=keep-id:uid=0,gid=0` means files the build creates come out owned by
you rather than by a container uid, so there is nothing to migrate and nothing
to sync back. Editing on the host and building in the workspace is the same
tree either way.

What that leaves is a class of bug rather than a design question: **config keys
that name HOST paths do not resolve inside the container.** Three have bitten
so far, all the same shape.

| key | host value | inside |
|---|---|---|
| `PORTHOLE_WORKDIR` | `~/src/.../taimen` | remapped to `/work` |
| `PORTHOLE_DEVICE` | from the environment, a layer that stops at the boundary | passed in explicitly |
| `PORTHOLE_PMBOOTSTRAP_SRC` | `~/src/pmbootstrap` | remapped to `/opt/pmbootstrap-src` |

The third one was invisible until the container was asked to build: the Alpine
`pmbootstrap` package installs the `pmb` python package and **no `helpers/`**,
while `helpers/envkernel.sh` — which every rung compiles through — exists only
in the source repo. So the workspace had a working `pmbootstrap` CLI and could
not build a kernel at all. The image now clones the source at the tag matching
the installed CLI, and the build fails if that helper is absent.

**The rule this leaves behind:** a new path-valued config key must be mounted,
remapped, or deliberately unset for the container. Nothing catches this
automatically yet, and every instance so far presented as an unrelated error
somewhere deep in a build.

### 5. Verbs

| verb | does |
|---|---|
| `sandbox build` | `podman build -t porthole-sandbox:<VERSION>`; idempotent, skips when the tag exists unless `--force` |
| `sandbox up` | build if needed, create the container, generate the device key |
| `sandbox shell` | `podman exec` into it; **`-it` only when `stdin.isatty()` and no `--command`** — this is what makes it reachable from an agent at all |
| `sandbox status` | as today, plus image presence, container state, staleness against `VERSION` |
| `sandbox down` | stop and remove the container; volumes untouched |

`porthole build` routes into the container when it exists, on the host
otherwise, with a one-line note saying which. `--host` forces the old path.

### 6. Storage

The workdir stays a **bind-mounted host directory** (`PORTHOLE_PMB_DIR`), so it
grows with the filesystem. No volume manager, no growable image, no grow
daemon — those add a failure mode without removing the real one, which is that
the backing disk fills.

What is added instead:

- a **measured** budget per rung, seeded from the numbers above
- a **preflight check** that refuses to start a build that cannot finish, and
  names both the shortfall and the config knob that relocates the workdir
- `doctor` reporting free space against that budget

Failing in one second with a number beats failing at minute forty with
`ENOSPC`.

### 7. Host bootstrap

`podman` is the only thing that needs a package manager. `git` and `python3`
are assumed. Everything else is automated in, or lives in the image.

`porthole doctor --fix` gains:

- **ostree/atomic detection.** Today `PACKAGES["fastboot"]["fedora"]` is
  `sudo dnf install android-tools`, and the reference host — Fedora Silverblue
  — has no `dnf`. The toolbox currently prints advice that cannot work on the
  machine it was developed on.
- image presence and staleness
- the one-time `qemu-user-static` / binfmt step, **named but never automated**:
  it is host-global and needs root once. That is a person installing software
  on their own computer, not a privilege the agent holds.

The package manager stays a *hint*. It is never a dependency.

### 8. `ph-sudo` is removed

**Superseded 2026-08-29: demotion became deletion.** This section originally
demoted the broker to an opt-in fallback for a host without podman. That is no
longer the design -- `sandbox/ph-sudo`, `sandbox/ph-sudo-client`,
`tests/test_sandbox.py`, the `install`/`audit`/`uninstall` verbs and every
mention of `PMB_SUDO` as a knob are gone.

The reasoning that made it a fallback is the reasoning that removed it. A
broker confines the directory, not the payload, and pmbootstrap legitimately
needs to write executables into a chroot and then run them -- so an allowlist
permissive enough for pmbootstrap is permissive enough for anything already
inside that chroot. It bought a real sudoers entry and false confidence.

The operational argument was the decisive one: a weaker path that still exists
is the one a stuck agent reaches for, and every reader had to work out which
tier they were on before they could trust anything. Podman is now the single
host prerequisite.

`PMB_SUDO` is a hard failure in `porthole doctor` rather than a knob:
pmbootstrap invokes it directly, so a leftover export kills a build with exit
78 deep inside pmbootstrap, naming nothing.

### 9. Propagation — done, 2026-08-29

This section was a table of places that still presented `ph-sudo` as a co-equal
tier and had to be edited. It is kept as a record of the method, not as work
outstanding: **the broker was removed rather than demoted, and every entry
below has landed.**

`AGENTS.md`, `README.md`, `docs/SANDBOX.md`, `skills/porthole-bringup/SKILL.md`
and `brain/traps/a-long-sudo-cache-is-unlimited-root.md` now describe one
boundary and no fallback. `sandbox/ph-sudo`, `sandbox/ph-sudo-client` and
`tests/test_sandbox.py` are deleted; the `install`, `audit` and `uninstall`
verbs are gone from `lib/porthole_cmd_sandbox.py`.
`tests/test_sandbox_container.py` and `tests/test_doctor.py` now assert the
ABSENCE -- files, symbols and verbs -- so a re-introduction fails the suite
rather than passing quietly.

`tests/test_config.py` and `tests/test_tui_safety.py` match on the word
"sandbox" for unrelated reasons and were **not** targets. Recorded so the next
person does not re-derive it.

The rule that made this work is worth keeping: **grep, do not remember.** The
list was built with `grep -rln 'ph-sudo\|PMB_SUDO\|timestamp_timeout'` and
should be rebuilt the same way, because this file is out of date the moment
someone adds a reference.

## What this does not do

- **Protect the device.** Neither tier ever did. The device mutex, the
  forbidden-slot guard and confirm-before-irreversible are what cover that.
- **Rootful podman.** Rejected, not overlooked. Root in a rootful container is
  real root in the initial user namespace, and `/dev/loop-control` plus
  `CAP_SYS_ADMIN` is itself a host-write primitive. Brokering the *command*
  does nothing about the *payload*, and `pmbootstrap install` runs APKBUILD
  scripts and apk triggers as root.
- **Remove the one-time binfmt step.** Host-global, needs root once, stays a
  human action.

## Tests

Alongside `tests/test_sandbox.py`, whose escape attempts stay as they are:

- the Containerfile pins a base tag rather than `latest`
- `sandbox build` is idempotent — a second call with no `--force` runs nothing
- `sandbox shell` omits `-it` when `--command` is given (regression: the agent path)
- `sandbox shell` mounts the device-mutex lock path whenever it mounts anything
- `sandbox up` never mounts `~/.ssh`, and mounts the dedicated key read-only
- the preflight space check refuses when free space is below the budget, and
  its message names `PORTHOLE_PMB_DIR`
- `doctor` on a simulated ostree host does not emit a `dnf` command
- a probe test recording that the device rootfs chroot contains **no** device
  nodes, so the day that changes, the `mknod`/FUSE incompatibility surfaces as
  a failure rather than as a corrupt image
- a probe test recording the fuse2fs mount capability, so a kernel or podman
  release that changes the answer shows up as a failure rather than folklore
- a 4-distro `doctor --fix --dry-run` smoke (Debian, Arch, Alpine, Fedora),
  extending `tests/ci-local.sh`, so "works on any distro" is verified rather
  than asserted
- `brief` on a host with no workspace emits the onboarding line (§10), because
  a prompt nobody sees is the same as no prompt
- nothing writes a sudoers entry at all: the broker that used to is deleted
- a grep guard: no shipped doc, skill or verb offers the broker. `tests/
  test_sandbox_container.py` and `tests/test_doctor.py` assert the absence of
  the files, symbols and verbs, so a re-introduction fails the suite, and it
  is what stops §9 from silently rotting

## Phases

**Phase 4 and §10's skill text were pulled forward and landed on 2026-08-29.**
The original ordering put the demotion last, reasoning that the posture should
land once nothing needed the broker. That was right for code and wrong for
docs: phase 1 shipped the replacement, so every agent-facing document was then
actively recommending the thing it replaced. An agent loading
`skills/porthole-bringup/SKILL.md` learned nothing about the workspace at all.
The lesson is narrow and worth keeping: **guidance has to move in the same
commit range as the thing it describes, or it is wrong in the interval.**


Deliberately sequenced; each phase is useful alone.

| # | what | why here |
|---|---|---|
| **0** | probes — **done, 2026-08-29**, results above | the design rested on them |
| **1** | Containerfile, `sandbox build`/`up`/`shell`/`down`, persistence, the `-it` fix, mounts, device key | nothing else is testable without it |
| **2** | `doctor --fix`, ostree awareness, the distro smoke, **the `brief` onboarding line** | the bootstrap claim verified, and an agent that can actually ask for what it needs |
| **3** | fuse2fs shim, `porthole build` routing, preflight space check | the actual build loop |
| **4** | ~~default install grants no sudoers~~ · ~~§9 propagation~~ · ~~`docs/SANDBOX.md` rewritten~~ — **done 2026-08-29**, ahead of its phase | the security posture lands once nothing needs it — and lands *everywhere*, or it has not landed |

Phase 4 is the one most likely to be declared done while half-finished, because
its work is spread across nine files instead of concentrated in one. Re-run the
grep from §9 before calling it.

## Open

- ~~Which Alpine tag~~ — **answered 2026-08-29: 3.24**, the newest release, and
  it carries `pmbootstrap-3.11.1-r0`, which is the newest upstream release
  tag. Verified in a container that it has `--no-image` and the
  `if no_image: return` early return. Alpine **3.22** would have given 3.9.0,
  and PyPI tops out at 2.1.0 — so the packaged tool is only the latest on a
  current base, and bumping the base is how the tools stay current.
- How the image is rebuilt when `VERSION` moves.
- Whether the fuse2fs shim is a patch carried in `sandbox/`, or a
  `PMB_*`-style hook proposed upstream. Upstream is better; carrying it is
  faster.
- `pmbootstrap install --no-image` is **source-verified, not run-verified** —
  running it remints filesystem UUIDs and forces a rootfs reflash, so it was
  deliberately not run during phase 0. Phase 3 must verify it on a build that
  is going to be flashed anyway.
