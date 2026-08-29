---
id: fuse2fs-cannot-replace-the-loop-device
title: fuse2fs cannot stand in for the loop device, because the loop device is exposing a partition table
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Read of pmbootstrap 3.11.1 -- an EXTERNAL checkout, not this repo, so\n  re-check it there rather than here: `grep -rn \'dev/install\' <pmbootstrap>/pmb/`\n  shows the loop device bound in as /dev/install, parted creating the partition\n  nodes from its table, and mkfs plus mount consuming those nodes as block\n  devices. fuse2fs was separately verified working on a plain ext4 file in a\n  rootless container on 2026-08-29."
refutes: "a fuse2fs shim over losetup makes pmbootstrap's image build work rootless; replacing losetup+mount is a small change; docs/SANDBOX-PROVISIONING.md §4 as originally written"
first-learned: 2026-08-29
---

**The question** — the sandbox design needs `pmbootstrap install` to build a
flashable image without a loop device, because a rootless container cannot have
one. `fuse2fs` mounts ext4 from a plain file inside a user namespace, which
looked like the answer. Is it?

**The answer — no, and the reason is structural rather than a missing feature.**

`fuse2fs` mounts a **filesystem**. The loop device is not being used to mount a
filesystem: it is being used to expose a **partitioned disk** so the kernel
will create partition block devices from its table.

The chain, in pmbootstrap 3.11.1:

| step | what it needs |
|---|---|
| `pmb/chroot/mount.py:34` | binds the loop device into the chroot as `/dev/install` |
| `pmb/install/partition.py:58-61` | runs `parted` on `/dev/install`, which makes the KERNEL create `/dev/installp1`, `p2`, `p3` |
| `pmb/install/format.py:260-266` | runs `mkfs` on `/dev/installp1` and `/dev/installp2` — partition devices, not files |
| `pmb/install/partition.py:16-41` | mounts those partition devices |

There is no point in that chain where a filesystem image is mounted from a
plain file. Every consumer wants a **partition block device**, and only the
kernel's partition scanner produces one — which is what the loop device is
there to trigger. `fuse2fs` cannot produce `/dev/installp1`, so there is
nothing for it to substitute for.

`--split` does not help: it creates separate boot and root images and then
calls `losetup.mount` on each of them anyway.

**What this rules out** — the "shim" framing. `docs/SANDBOX-PROVISIONING.md` §4
described replacing `losetup` with a no-op and `mount /dev/loopN` with
`fuse2fs <img> <mnt>`, and said pmbootstrap would keep owning partition layout
and fstab. That is not a change that can be made: the code between those two
points is the part that needs a block device.

**What is still true, and is the real opening.** Two facts survive:

- `pmbootstrap install --no-image` never reaches any of this. `_install.py`
  returns before `install_system_image()`, which is the only caller of
  `blockdevice.create()`. Verified by reading, not run.
- `mkfs.ext4` does not need a block device. It works on a plain file, and
  `mkfs.ext4 -d <dir>` will POPULATE a filesystem from a directory with no
  mount at all — no loop device, no fuse, no privilege.

So an unprivileged image build is still possible; it is just a different shape.
Build each partition as its own filesystem image with `mkfs.ext4 -d`, then
assemble the combined image by hand: `truncate` it, write the table with
`sfdisk` (which operates happily on a regular file), and `dd` each filesystem
image to its offset.

**The cost of that shape, stated plainly**, because it is the reason the shim
was preferred in the first place: porthole would then own the partition layout
and the filesystem UUIDs that `boot.img` hard-codes in its cmdline. `ph-build.sh:510`
records a real device failure from exactly that pair disagreeing — a boot.img
wanting `6805a9af-…` while the device held `39056921-…`, which presents as a
phone dropping to a telnet debug shell. Duplicating that logic is how it drifts.

**Not yet established** — whether `mkfs.ext4 -d` reproduces what pmbootstrap's
own `mkfs` invocation produces closely enough (it passes `-O` feature flags and
a label; see `pmb/install/format.py:40-49`), and whether the assembled image boots. Both
need a real build, which needs the pmbootstrap work directory — so neither was
run while another agent was using it.
