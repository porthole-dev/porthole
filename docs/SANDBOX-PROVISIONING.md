# Sandbox provisioning — design

**Status:** designed, not built. The iteration ladder shipped 2026-08-26; this is the other half.

## The problem

An agent doing a bring-up needs pmbootstrap, adb/fastboot, a kernel toolchain
and a device. Today it gets those by being handed the host — usually via
`Defaults:you timestamp_timeout=9999`, the 167-hour root cache
`docs/SANDBOX.md` exists to argue against.

The container tier was supposed to be the answer, and it is not usable yet:

- `lib/porthole_cmd_sandbox.py:456` runs `docker.io/library/alpine:latest` with
  `--rm`. Bare alpine: no pmbootstrap, no android-tools, no git, no python.
  Nothing is installed and `--rm` discards anything installed at runtime.
- `-it` is unconditional, so `porthole sandbox shell --command ...` from an
  agent with no TTY fails before it starts.
- No verb provisions an image. There is nothing to provision.

So "build the whole thing in the sandbox" is not currently possible, and the
documented fallback is plain sudo on the host.

## What was measured, 2026-08-26

Probed on the reference host (Fedora 44, podman 5.8.4 rootless, kernel 7.1.7).
**Re-run these before trusting them on another setup** — one trace is not
enough was the lesson from the broker, and it applies here too.

| capability, rootless `--userns=keep-id:uid=0,gid=0` | result |
|---|---|
| USB device tree — `lsusb` in-container saw the Pixel 2 XL (`18d1:d001`) | works |
| `fastboot` binary, `apk add android-tools` | works; same result as host |
| binfmt `qemu-aarch64` | already registered host-side, inherited |
| `mknod` a loop node | **denied** |
| `losetup` attach (`LOOP_SET_FD`), no node passed | **denied** |
| `losetup` attach with host-allocated `/dev/loop0` passed via `--device` | **denied** |

The loop denial is a kernel boundary, not a configuration knob. `LOOP_SET_FD`
and block-device `mount` require `CAP_SYS_ADMIN` in the **initial** user
namespace; a rootless container never holds that, whatever it is granted inside
its own. Passing the node in does not help — it is `root:root 0600`, and even
`chmod 0666` would not confer the capability.

Reproduce:

```sh
truncate -s 64M /tmp/probe.img
podman run --rm --userns=keep-id:uid=0,gid=0 --cap-add SYS_ADMIN,MKNOD \
  --device /dev/loop-control -v /tmp/probe.img:/img alpine sh -c '
    apk add -q util-linux; d=$(losetup -f); losetup $d /img'
# losetup: /dev/loop0: failed to set up loop device: Permission denied
```

### What that means for the split

`pmbootstrap install` is the only step that needs loop devices, and it is
called from exactly one place: `tkbuild`, the **top** rung of the ladder.

| rung | needs loop devices | container |
|---|---|---|
| `mod` | no | yes |
| `boot` | no | yes |
| `fast` (`tkbuild-kernel`) | no | yes |
| `kernel` (`tkbuild`) | **yes** (`pmbootstrap install`) | no — host |

So the three rungs an agent iterates on are fully containerisable, and only the
slowest one is not. That is a far better outcome than the raw denial suggests,
and it is the reason this design is worth building rather than abandoning.

Flashing **does** work in the container: USB passthrough is a bind-mount and
the device node's ACL already grants the invoking uid. That was the part
expected to be hard and was not.

## Design

### 1. `sandbox/Containerfile`

Alpine base, plus: `pmbootstrap`, `android-tools` (adb/fastboot), `git`,
`python3`, `openssl`, `xz`, `tar`, `util-linux`, `openssh-client`, and the
build deps envkernel expects. Pinned base tag, not `latest` — a sandbox that
changes under you is not a sandbox.

### 2. `porthole sandbox build`

`podman build -t porthole-sandbox:<VERSION>` from that file. Idempotent: skips
when the tag exists unless `--force`. VERSION comes from `VERSION`, so an image
is traceable to a toolbox revision.

### 3. `_shell` fixes

- Default `IMAGE` becomes the local tag; when absent, fail with "run
  `porthole sandbox build`" rather than silently pulling bare alpine.
- `-it` only when `stdin.isatty()` and no `--command`. This is what makes the
  container reachable from an agent at all.
- Mount `/dev/bus/usb` so adb/fastboot work.
- **Mount the device-mutex lock directory.** `tools/tk-device.sh` flocks a path
  under `/tmp`; a container with its own `/tmp` gets its own lock, and a
  containerised agent and a host agent would then drive the phone
  simultaneously. This is a correctness requirement, not a convenience —
  `brain/laws/the-lock-says-who-not-what.md`.

### 4. `porthole sandbox install` does the unprivileged half

Builds the image, checks podman and binfmt, reports each as it goes, and still
writes the privileged script for the human to read and run. Installing a
security boundary stays a decision the developer makes; everything that needs
no privilege stops being their problem.

### 5. `porthole build` routes automatically

Runs in the container when the image exists, on the host otherwise, with a
one-line note saying which. `--host` forces the old path.

`kernel` is the exception: in-container it must **refuse**, naming the loop
device reason and pointing at the host command. Refusing is right rather than
transparently shelling back out to the host — a verb that silently escapes its
sandbox teaches the developer the sandbox is advisory, and the whole value of
the broker/container design is that its boundaries are real. One extra typed
command is a fair price for that being true.

*(This was the open question raised on 2026-08-26; recorded here as decided,
and worth re-opening only with a reason.)*

## What this does not do

- **`pmbootstrap install` in the container.** Kernel boundary, measured above.
  Stays on the host, via the broker or an interactive sudo.
- **Rootful podman.** `sudo podman` hands back the whole host and undoes the
  point. Explicitly rejected, not overlooked.
- **Protect the device.** Neither tier does. The mutex, the forbidden-slot
  guard and confirm-before-irreversible are what cover that.

## Tests

Alongside `tests/test_sandbox.py`, whose escape attempts stay as they are:

- the Containerfile pins a base tag rather than `latest`
- `sandbox build` is idempotent — a second call with no `--force` runs nothing
- `_shell` omits `-it` when `--command` is given (regression: the agent path)
- `_shell` mounts the mutex lock path whenever it mounts anything
- `build kernel` inside the container refuses, and the message names loop
  devices rather than a generic permission error
- a probe test that records the loop denial, so the day a kernel or podman
  release changes the answer, that shows up as a failure rather than as folklore

## Open

- Which base tag, and how the image gets rebuilt when `VERSION` moves.
- Whether `porthole doctor` should report image presence and staleness. Likely
  yes — it is the verb that already answers "will the toolbox work".
