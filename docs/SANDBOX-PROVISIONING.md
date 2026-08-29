# Sandbox provisioning — design

<!-- porthole:design-doc -- the verbs below are proposed, not built -->

**Status:** designed, not built. Supersedes the 2026-08-26 revision of this
file, whose central conclusion was wrong. Measurements re-run 2026-08-29.

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
`18d1:d001`), then `tools/tk-to-fastboot.sh` under the device mutex. The old
node vanished, node `004` appeared as `18d1:4ee0`, and the **already-running**
container ran `fastboot devices` and `fastboot getvar current-slot` against it
successfully.

Reproduce:

```sh
podman run -d --name probe --userns=keep-id:uid=0,gid=0 \
  --security-opt label=disable -v /dev/bus/usb:/dev/bus/usb alpine:3.22 sleep 3600
podman exec probe apk add -q android-tools
TK_AGENT=you tools/tk-device.sh --need-booted tools/tk-to-fastboot.sh
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
`/tmp/porthole-<device>.lock`, the same path `tools/tk-device.sh` computes, and
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

### 8. `ph-sudo` is demoted

With the container covering chroot and package work, the broker's most
dangerous permission — `allow_chroot`, which its own documentation admits does
not contain a determined payload — has no remaining caller.

It stays in the tree, tested, and **stops being installed by default**. It
becomes a documented opt-in fallback for a host without podman, with its weaker
guarantee stated plainly. The default install writes **no sudoers entry at
all**.

### 9. Propagation — the demotion must land everywhere

A demotion that lives only in this file is not a demotion. `ph-sudo` is
currently presented as a co-equal tier in the places an agent and a newcomer
actually read, and every one of them has to move. Inventoried, not guessed:

| where | what is there now | must become |
|---|---|---|
| `AGENTS.md:113-117` | "Never ask for host root outside the sandbox" offers `sandbox shell` **or** the brokered `PMB_SUDO` as equals | the container is the answer; the broker is a named fallback with its limit stated |
| `AGENTS.md:300` | verb table row for `sandbox` | new verbs (`up`, `down`, `build`) |
| `README.md:408-416` | leads with the broker, `sandbox install` | leads with `sandbox up`; the broker moves below the fold |
| `docs/SANDBOX.md` | the two-tier threat model, tiers presented as complementary | rewritten: one tier, zero standing privilege, broker as opt-in legacy |
| `brain/traps/a-long-sudo-cache-is-unlimited-root.md` | the warning is still correct; the **remedy** points at the broker | remedy points at the container; keep the trap, change the fix |
| `skills/porthole-bringup/SKILL.md` | **no mention of the sandbox at all** | see §10 — this is the onboarding hole |
| `lib/porthole_cmd_sandbox.py` | `install` writes a sudoers entry by default | default install writes none |
| `lib/porthole_cmd_docs.py:375,429` | docs-site nav for `SANDBOX.md` | add the provisioning page |
| `tests/test_sandbox.py` | escape attempts against the broker | keep every one; add the §Tests list |

`lib/porthole_cmd_next.py:202` and `lib/porthole_milestones.py:80` mention the
boundary only in passing comments and need re-reading, not necessarily editing.
`tests/test_config.py` and `tests/test_tui_safety.py` match on the word
"sandbox" for unrelated reasons and are **not** targets — recorded here so the
next person does not re-derive that.

The rule for this pass: **grep, do not remember.** The list above was built by
`grep -rln 'ph-sudo\|PMB_SUDO\|timestamp_timeout\|sandbox'` and should be
rebuilt the same way before phase 4 is called done, because this file will be
out of date the moment someone adds a reference.

### 10. An agent must be able to onboard onto an unprepared host

The toolbox is useless if the agent cannot tell that the host is not set up, or
tries to fix it by reaching for sudo. Three requirements, and the third is the
one that is missing today.

**Detect.** `porthole doctor` reports the workspace the same way it reports
every other prerequisite: podman present, image built, container running,
device key installed, free space against the budget.

**Surface it unprompted.** `porthole brief` — the verb `AGENTS.md` tells every
agent to run first — gains a line when the workspace is absent or stale.
`brief` already carries "anything ticked that a probe says is not true"; an
unprepared sandbox belongs in exactly that block, not behind a verb the agent
would have to know to run.

**Ask, never escalate.** The agent's correct move on an unprepared host is to
**stop and ask the human to run `porthole doctor --fix`**, because the one
remaining privileged step (installing podman, and the one-time binfmt
registration) needs a password an agent cannot and must not type. This has to
be written into `skills/porthole-bringup/SKILL.md`, which today says nothing
about the sandbox at all — so an agent loading the skill for a bring-up would
never learn any of this exists.

The message the agent gives the human should be one copy-pasteable block:

```
This host has no porthole workspace yet. Please run:

    porthole doctor --fix

It will install podman if missing, build the sandbox image, and name the
one-time binfmt step. It will ask for your password for those, and only those.
I cannot run it for you, by design.
```

`doctor --fix` prints every command before running it and never runs a
privileged one without consent — the same posture `sandbox install` already
takes, and for the same reason: installing a boundary should be a decision the
developer makes.

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
- `sandbox install` writes **no** sudoers entry unless the legacy broker is
  explicitly requested
- a grep guard: no shipped doc or skill presents the broker as the primary
  path. `tests/test_tools.py` already fails when the CI step lists drift out of
  sync with the Makefile; this is the same idea applied to the demotion, and it
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
