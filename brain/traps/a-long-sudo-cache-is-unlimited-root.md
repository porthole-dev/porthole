---
id: a-long-sudo-cache-is-unlimited-root
title: A long sudo credential cache is unlimited root for every process you run
scope: generic
subsystem: setup
severity: trap
confidence: proven
evidence: porthole docs/SANDBOX.md; sandbox/ph-sudo; observed on the taimen host 2026-08-23
first-learned: 2026-08-23
---

pmbootstrap needs root, and the obvious way to stop it prompting is:

```
Defaults:you timestamp_timeout=9999
```

**That is a 167-hour root credential cache**, and it is not scoped to
pmbootstrap. It is scoped to *you*. For a week, anything running under your
account gets silent root with no prompt, no allowlist and no audit trail: an
agent, a build script, a compromised dependency, a prompt injection that arrived
in a log file something read.

The failure mode is not "the agent turns evil". It is that the blast radius of
**any** mistake, from any source, becomes the whole machine — and you cannot
afterwards tell what used it.

**The intercept.** pmbootstrap escalates through exactly one hook:

```python
def sudo(cmd):
    sudo = which_sudo()          # honours $PMB_SUDO
    return [sudo, *cmd] if sudo else cmd
```

So `PMB_SUDO=<broker>` routes every root request through a program you control,
as argv, which you can validate. It is a supported upstream feature.

**The surface is smaller than it looks.** A real `pmbootstrap chroot -- true`
plus `shutdown` makes 72 root requests across 11 verbs (mount, umount, sh,
mknod, chmod, ln, rm, mkdir, touch, env, losetup), and **every path argument is
inside the pmbootstrap work directory**. Measure it on your own setup rather
than trusting that list.

**The remedy is to need no root at all.** `porthole sandbox up` runs the work
in a persistent rootless container where you are uid 0 inside and your own
unprivileged uid outside, so pmbootstrap uses no sudo whatsoever and no sudoers
entry is granted. An escape reaches your uid, not the machine.

A validating broker on `PMB_SUDO` (confining paths, refusing non-pmbootstrap
verbs, auditing every decision) remains available for a host that cannot run
podman, and it is far better than a blanket cache — but it is a fallback, not a
second tier.

**Be honest about the gap in that fallback.** `chroot <dir> <cmd>` runs an
arbitrary command as root, and root inside a chroot can escape a chroot. A
broker confines the directory, not the payload. Claiming otherwise is worse
than not having the broker, because it buys false confidence. That gap is the
reason the container is the answer rather than the second half of one.

**The one thing worth doing even if you do nothing else:** delete the
`timestamp_timeout` line. A five-minute cache instead of a week-long one turns
every escalation after the first into one you were present for.

Generalises past pmbootstrap: the same shape applies to any agentic workflow
that needs a privileged tool — Docker socket access, `terraform apply`,
`kubectl` with cluster-admin. Ask what the tool actually needs, discover it
empirically, and broker exactly that.

Related: [[no-passwordless-sudo-disables-the-whole-toolbox]],
[[running-a-device-script-on-the-host]].
