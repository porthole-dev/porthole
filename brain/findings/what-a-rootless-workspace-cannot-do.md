---
id: what-a-rootless-workspace-cannot-do
title: Four things a rootless container cannot do that pmbootstrap assumes, and what each one costs
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Reference host 2026-08-29, podman 5.8.4 rootless, --userns=keep-id:uid=0,gid=0, subuid base 524288, kernel 7.1.10. Probed in the workspace: `mknod /tmp/n c 1 3` and `mknod /pmb/n c 1 3` both EPERM although CapEff carries CAP_MKNOD; `mount --bind /dev/null <file>` succeeds and reports 1:3, but `open(O_CREAT)` on the result is EACCES while plain O_WRONLY succeeds; `mount --rbind /dev <dir>` gives working nodes with correct major/minor, `mount --bind` gives 0:0; `chown` on a file owned by an unmapped uid is EPERM; pmbootstrap 3.11.1 (EXTERNAL to this repo -- re-check it in the pmbootstrap source) refuses uid 0 before reading its config. A full kernel build then ran in the workspace on a clean worktree."
refutes: "a rootless container just needs CAP_MKNOD to create device nodes; bind-mounting device nodes one by one gives a working /dev; a work directory built by host root can be chowned into a rootless container; --userns=keep-id maps enough uids to reuse an existing chroot; the workspace only needed the build routing to be finished"
first-learned: 2026-08-29
---

**The question** — the workspace container starts, mounts everything, and has
pmbootstrap installed. What actually stops it building, and what is the fix
for each?

Four things, each of which surfaces as an error naming something else.

**1. pmbootstrap refuses uid 0.** pmbootstrap raises "Do not run pmbootstrap as
root!" before it looks at the config -- in its own `__init__`, EXTERNAL to this
repo, so re-check it there. In here uid 0 IS the
user's unprivileged uid outside, so the refusal is wrong exactly here.
`--as-root` skips it. The wrapper must also replace the
pmbootstrap source checkout's own entry point, because envkernel calls that by
absolute path rather than off PATH -- miss it and the first kernel build hits the refusal with
everything else already working.

**2. `mknod` is denied, and binding the nodes one at a time is a trap.** Not a
missing capability: CapEff carries CAP_MKNOD and the kernel refuses anyway, on
container storage and bind mounts alike. `mount --bind /dev/null <file>`
succeeds and the node reports the right major and minor -- **and then
`open(O_CREAT)` on it returns EACCES while plain `O_WRONLY` works.** O_CREAT is
what every shell redirection uses, so the node looks perfect until something
writes `> /dev/null` and apk dies with "can't create /dev/null: Permission
denied".

`mount --rbind /dev <chroot>/dev` has none of that. **--rbind, not --bind**:
the per-node mounts podman layers into that tmpfs are what carry the real
major/minor, and a plain bind reports 0:0, which pmbootstrap's own verification
rejects. Stack it ON TOP of the tmpfs pmbootstrap mounts there
(`mount_dev_tmpfs`) -- skipping when the target is already a mountpoint is
wrong, because it always is.

**3. `chmod` and `chown` fail on anything an unmapped uid owns.** pmbootstrap
chmods each device node right after creating it, and a bound node belongs to
the host's `/dev`, so that chmod is EPERM even when the mode it asks for is the
mode already there. Tolerate the no-op case only.

The same rule is why **a work directory built by host root cannot be reused or
converted**: its files belong to uids this namespace cannot see, so they cannot
be chowned, and `chown -R` would flatten the chroots' internal uids anyway. The
workspace needs a work dir it created itself. Same for a kernel tree's
`.output`: it belongs to ONE environment, and the other must be told rather
than left to fail inside kbuild with `mkdir: can't create directory '.tmp_174'`.

**4. There is no `sudo`, and scripts call it anyway.** envkernel does, for its
`mount --bind` of the kernel tree. `sudo: command not found` reads as a broken
image rather than as a script asking for something it already has. A shim that
runs the command as who we already are -- and refuses if we are somehow not
root -- grants nothing.

**What this rules out** — that the workspace was one routing change away from
working. It had never compiled anything. Also that any of this is fixable by
adding capabilities: every one of these is a user-namespace rule, not a
permission bit.

**How it was established** — one probe per claim in the running container
(`porthole sandbox shell --command`), then a full kernel build end to end on a
clean git worktree, which is the case with no inherited ownership. The negative
control is the same build on a tree the host had built: it refuses, by design.
