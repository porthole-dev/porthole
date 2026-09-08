---
id: aports-start-bases-a-branch-on-the-channel-not-your-work
title: "`porthole aports start` bases the new branch on the channel, so your port's commits vanish from the working tree"
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "2026-09-08, pmaports at /home/user/.local/var/pmbootstrap/cache_git/pmaports, on taimen-bringup with a clean tree. `porthole aports start perf/crossdirect-native-link --yes` reported `on perf/crossdirect-native-link (from origin/main)`. Immediately after: `git log --oneline origin/main..taimen-bringup | wc -l` = 255, and `ls temp/webkit2gtk-6.0/APKBUILD` -> No such file or directory, i.e. the tuned webkit aport this port builds was no longer in the working tree. Cause read from source: lib/porthole_cmd_aports.py:221, `base = args.base or _channel_branch(ctx, pmaports) or current` -- the channel (systemd-edge) resolves to origin/main, and the current branch is only the last fallback. Recovered with `git checkout taimen-bringup && git branch -D <topic> && git checkout -b <topic> taimen-bringup`, after which the 255 commits and the aport were back."
first-learned: 2026-09-08
---

**The symptom** — you branch for a small change, and a package you know is
in pmaports is suddenly not on disk. A build that worked ten minutes ago now
says the aport does not exist, or quietly builds an upstream version of a
package your port had forked and tuned.

**The cause** — `aports start` is designed for work you intend to send
upstream, so it bases the new branch on the *channel's* branch, not on the
branch you were standing on. `lib/porthole_cmd_aports.py:221`:

    base = args.base or _channel_branch(ctx, pmaports) or current

The current branch is the last fallback, reached only when the channel cannot
be resolved. On this port the channel is systemd-edge, which resolves to
`origin/main`, so a branch taken off `taimen-bringup` lands 255 commits
behind and without any of `temp/`.

This is correct for its purpose. The docstring a few lines below explains it:
a branch aimed at edge should not end up based on a stable release. The trap
is that nothing about "start a feature branch" suggests the working tree is
about to lose your port, and pmaports is a **shared** checkout -- another
agent's build picks up the new branch too.

**Filed as a defect, not just a hazard** — porthole#79. `aports start` knows
the base and the current branch, so `git rev-list --count <base>..<current>`
would tell it exactly how much work is about to leave the tree; it should say
so before switching rather than after. Until that lands, this note is the
warning. An unenforced rule is a defect, not documentation.

**What to do instead** — when the change has to be *built locally* against
the port's own tree, name the base explicitly:

    porthole aports start <topic> --base taimen-bringup --yes

Use the default, channel-based form only for a change you are preparing to
send upstream and do not need to build against your device's packages.

**How to notice you have already done it** — the command prints its base, and
that line is the whole warning:

    on <topic> (from origin/main)

If that base is not what you expected, check before building:

    git log --oneline origin/main..<your-branch> | wc -l

**Recovery is cheap if the branch has no commits yet** — `git checkout
<your-branch>`, delete the topic branch, and recreate it with the right base.
If you have already committed on the wrong base, rebase the topic onto the
right branch rather than recreating it, so the commit survives. Related:
[[the-porthole-checkout-is-shared-with-live-agents]] -- the same "a shared
checkout moved under someone" hazard, from the other direction.
