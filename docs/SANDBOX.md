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

## The interception point

pmbootstrap escalates through exactly one documented hook —
`pmb/config/sudo.py`:

```python
def sudo(cmd):
    """Adapt a command to run as root."""
    sudo = which_sudo()          # honours $PMB_SUDO
    return [sudo, *cmd] if sudo else cmd
```

So `PMB_SUDO=<something>` routes every root request pmbootstrap makes through a
program of your choosing, as argv. That is the whole basis of this design, and
it is a supported upstream feature rather than a hack.

## What pmbootstrap actually asks for

Measured, not guessed. A logging shim was put in `PMB_SUDO` and a real
`pmbootstrap chroot -- true` plus `pmbootstrap shutdown` was run:

```
72 root requests
 17  mount        17  umount       12  sh          7  mknod
  7  chmod         4  ln            3  rm          2  mkdir
  1  touch         1  env           1  losetup
```

Eleven verbs. **Every path argument was inside the pmbootstrap work directory**
— with one exception found later, below.

That is what makes a validating broker viable: the legitimate surface is small,
regular, and confined.

### One trace is not enough

That capture ran against a chroot that was *already initialised*. Running the
finished broker against a fresh one immediately hit a denial:

```
mount --bind /proc <workdir>/chroot_native/proc   ->  DENIED
```

A chroot cannot function without `/proc`, `/sys` and `/dev` bound into it, and
pmbootstrap does that on every chroot init. The first trace never showed it.

The fix was principled rather than a widening: for a **bind** mount the
*destination* must always be confined, and the *source* may additionally be one
of a short, exact list of kernel API filesystems. Host data — `/etc`, `/home`,
`/root`, `/` — stays refused, because binding host data into a chroot hands it
to whatever runs in there.

**The lesson worth taking: derive the policy from a trace, then test it end to
end against the real thing.** A denial is information; investigate it rather
than relaxing the rule that produced it.

Reproduce it on your own setup before trusting this list:

```sh
cat > /tmp/logsudo <<'EOF'
#!/usr/bin/env python3
import json, os, subprocess, sys
open("/tmp/argv.jsonl", "a").write(json.dumps(sys.argv[1:]) + "\n")
sys.exit(subprocess.run(["sudo", *sys.argv[1:]]).returncode)
EOF
chmod +x /tmp/logsudo
PMB_SUDO=/tmp/logsudo pmbootstrap chroot -- true
```

## Two tiers

Neither is sufficient alone, and they are complementary rather than
alternatives.

### Tier 1 — the broker (`ph-sudo`)

One sudoers entry, for one root-owned file:

```
you ALL=(root) NOPASSWD: /usr/local/libexec/porthole/ph-sudo
```

Everything pmbootstrap asks for arrives here as argv and is validated:

| check | why |
|---|---|
| verb is in the allowlist | 11 verbs, derived from the capture above |
| every path resolves inside a declared root | symlinks followed, `..` normalised |
| `sh -c` only for `echo TEXT >> confined/path` | a free-form shell command is root by definition |
| `mknod` only for standard chroot nodes | a block device inside a confined dir is a way out of it |
| `mount` rejects `remount`, `rbind`, `move` | each can relocate or re-flag a mount out of the roots |
| the binary lives in a system `bin` directory | otherwise the caller supplies the behaviour |
| every request appended to an audit log | allowed and denied alike, with the reason |

The policy lives at `/etc/porthole/sandbox.conf`, **root-owned**, and the broker
refuses to run if it is writable by anyone else. The broker itself must be
root-owned for the same reason: a boundary the agent can edit is theatre.

### Tier 2 — the container (`porthole sandbox shell`)

```sh
podman run --userns=keep-id:uid=0,gid=0 ...
```

Inside, you are root, so pmbootstrap uses **no sudo at all** — `which_sudo()`
returns `None` when `os.getuid() == 0`. Outside, that root is your own
unprivileged uid.

Verified on this setup: a file created by container-root came out owned by
uid 1000. `chroot` works with default capabilities; `mount --bind` needs
`SYS_ADMIN`, which inside a rootless user namespace confers nothing beyond that
namespace.

Only what you mount is reachable. Your ssh keys, `/etc`, other users' data and
the rest of the machine are not present.

## What each tier does not do

Being straight about this matters more than the feature list.

**The broker does not contain a determined chroot payload.**
`chroot <dir> <cmd>` runs an arbitrary command as root. The *directory* is
confined, but the payload is not — and root inside a chroot can escape a chroot.
So the broker stops accidents, mistakes, blast radius and casual misuse, which
is the overwhelming majority of real risk, and it leaves an audit trail. It is
not a barrier against an adversary who controls what runs inside the chroot.

Set `allow_chroot = 0` in the policy to refuse chroots on the host entirely and
do that work only in the container.

**The container does not cover everything.**

| | works in the container |
|---|---|
| chroot operations, package builds, apk work | yes |
| `pmbootstrap install` (needs loop devices) | no — needs real root on the host |
| binfmt registration for cross-arch | no — host-global, one-time human step |
| flashing a device | no — needs USB device access |

So the honest arrangement is: container for the day-to-day loop an agent
drives, broker for the host operations that genuinely need real root, and an
interactive password for the rare rest.

**Neither tier protects the device.** A phone you have given passwordless sudo
to is a phone that a tool can brick. That is what the device mutex, the
forbidden-slot guard and the confirm-before-irreversible rule are for.

## Setting it up

```sh
porthole sandbox status     # what is configured, what is missing
porthole sandbox install    # writes a script; read it, then run it
```

`install` deliberately does not run the privileged steps itself. Installing a
security boundary should be a decision you make rather than something a tool
does to you — and an agent cannot type a sudo password anyway, so handing it to
you is the correct behaviour rather than a limitation.

After running it:

```sh
export PMB_SUDO=/usr/local/libexec/porthole/ph-sudo   # add to your profile
sudo visudo    # DELETE the Defaults:you timestamp_timeout=<large> line
```

**That last step is the one that matters.** Installing the broker while leaving
the blanket cache in place buys nothing: anything can still call plain `sudo`.

Verify:

```sh
porthole sandbox status
PMB_SUDO=/usr/local/libexec/porthole/ph-sudo pmbootstrap chroot -- uname -a
porthole sandbox audit --denied
```

Measured on the reference setup: a full `pmbootstrap chroot -- uname -a` against
an uninitialised chroot brokered **31 root operations, all allowed**, and the
chroot ran. The same broker refused, with exit 77 and an audit entry each:

| attempt | refused because |
|---|---|
| `rm -rf /etc` | path escapes the declared roots |
| `touch /etc/cron.d/backdoor` | path escapes the declared roots |
| `sh -c 'cat /etc/shadow > /tmp/stolen'` | `sh -c` is only for a literal append |
| `mount --bind /home <chroot>/home` | bind source is host data, not an API filesystem |
| `bash -c id` | verb not in the allowlist |

## Reading the audit log

```sh
porthole sandbox audit             # last 40 decisions
porthole sandbox audit --denied    # only refusals
porthole sandbox audit --json      # for a machine
```

A denial is not necessarily an attack — more often it is a pmbootstrap version
doing something the allowlist has not seen. **Widen the policy deliberately when
that happens, and never to make an error go away.** If a new verb is needed, add
it to `sandbox/ph-sudo` with a test in `tests/test_sandbox.py` alongside, so the
reason is recorded.

## Testing

`tests/test_sandbox.py` runs unprivileged, executes nothing, and is mostly
escape attempts:

- a path outside the roots
- a sibling directory sharing a prefix (`/work` vs `/work-evil`)
- a symlink planted inside the root pointing at `/etc`
- `..` traversal
- an arbitrary shell command, and a correctly-shaped one aimed at `/etc/passwd`
- a device node for a raw disk
- `mount --bind /etc`, `mount -o remount`
- a chroot target outside the roots
- a "mount" binary supplied from a writable directory
- a policy file the invoking user can write

Those are the tests that matter. A broker that allows the right things is easy;
one that refuses the wrong things is the product.

## If you only do one thing

Delete the `timestamp_timeout` line. Even with no broker and no container, going
back to a normal 5-minute sudo cache shrinks a week-long window to a few
minutes, and every escalation after that is one you were present for.
