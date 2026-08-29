# Running pmbootstrap without handing over the host

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
require `CAP_SYS_ADMIN` in the *initial* user namespace. It matters less than
it sounds, because `pmbootstrap install --no-image` never touches a loop device
(`_install.py` returns before `install_system_image`), and `fuse2fs` mounts
ext4 from a plain file inside a user namespace, where ext4-on-loop is illegal.
See `docs/SANDBOX-PROVISIONING.md`.

## The legacy broker — `sandbox/ph-sudo`

**Not installed by default. Only for a host that cannot run podman.**

```sh
porthole sandbox install --broker    # writes a script; read it, then run it
```

Before the workspace existed, this was the answer: pmbootstrap escalates
through exactly one documented hook, `PMB_SUDO`, so pointing that at a
validating broker routes every root request through a program of your choosing,
as argv. One sudoers entry, for one root-owned file:

```
you ALL=(root) NOPASSWD: /usr/local/libexec/porthole/ph-sudo
```

It validates the verb against an allowlist, resolves every path argument inside
declared roots (symlinks followed, `..` normalised), permits `sh -c` only for a
literal append, restricts `mknod` to standard chroot nodes, rejects `remount`,
`rbind` and `move`, and appends every decision to an audit log
(`porthole sandbox audit --denied`).

**Why it is a fallback rather than a tier.** `chroot <dir> <cmd>` runs an
arbitrary command as root, and root inside a chroot can escape a chroot. The
directory is confined; the payload is not. So it stops accidents, mistakes,
blast radius and casual misuse — the overwhelming majority of real risk — and
leaves an audit trail, but it is not a barrier against an adversary who
controls what runs inside the chroot. Set `allow_chroot = 0` to refuse chroots
entirely; the workspace covers that work now, so on a host with podman there is
nothing left for the broker to do.

It also **cannot build a package**: building runs `$WORKDIR/apk.static` as
root, and the work directory is writable by the invoking user, so permitting an
executable out of it would let anyone who can write there be root. The broker
refuses, and that refusal is the boundary working rather than a gap.

### What pmbootstrap actually asks for

Measured, not guessed — a logging shim in `PMB_SUDO` across a real
`pmbootstrap chroot -- true` plus `pmbootstrap shutdown`:

```
72 root requests
 17  mount        17  umount       12  sh          7  mknod
  7  chmod         4  ln            3  rm          2  mkdir
  1  touch         1  env           1  losetup
```

Eleven verbs, every path argument inside the work directory — with one
exception found only by testing the finished broker against a *fresh* chroot,
which immediately hit `mount --bind /proc <workdir>/chroot_native/proc ->
DENIED`. A chroot cannot function without `/proc`, `/sys` and `/dev`. The fix
was principled rather than a widening: for a bind mount the *destination* must
be confined, and the *source* may additionally be one of a short list of kernel
API filesystems. Host data stays refused.

**The lesson worth keeping: derive the policy from a trace, then test it end to
end against the real thing.** A denial is information; investigate it rather
than relaxing the rule that produced it.

## Reading the audit log

```sh
porthole sandbox audit             # last 40 decisions
porthole sandbox audit --denied    # only refusals
porthole sandbox audit --json      # for a machine
```

A denial is not necessarily an attack — more often it is a pmbootstrap version
doing something the allowlist has not seen. **Widen the policy deliberately
when that happens, and never to make an error go away.**

## Testing

`tests/test_sandbox.py` runs unprivileged, executes nothing, and is mostly
escape attempts against the broker — a path outside the roots, a sibling
directory sharing a prefix (`/work` vs `/work-evil`), a symlink planted inside
the root, `..` traversal, a shell command aimed at `/etc/passwd`, a device node
for a raw disk, `mount --bind /etc`, `mount -o remount`, a chroot target
outside the roots, a "mount" binary supplied from a writable directory, and a
policy file the invoking user can write.

Those are the tests that matter. A broker that allows the right things is easy;
one that refuses the wrong things is the product.

`tests/test_sandbox_container.py` covers the workspace, and its assertions are
the same idea: that the mount set never exposes `~/.ssh`, that the device key
is read-only, that the lock path matches `tools/tk-device.sh` exactly, that
`--command` omits `-it`, that `down` never passes `-v`, and that a failed
`podman inspect` refuses rather than reading as "no drift".

## If you only do one thing

Delete the `timestamp_timeout` line. Even with no workspace and no broker,
going back to a normal 5-minute sudo cache shrinks a week-long window to a few
minutes, and every escalation after that is one you were present for.
