---
id: stacked-bind-mounts-break-pmbootstrap
title: Every `source envkernel.sh` stacks another /mnt/linux bind mount
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: taimen tools/taimen-build.sh tkclean; stacked 16 deep, 2026-07-25
first-learned: 2026-07-25
---

`source envkernel.sh` bind-mounts the kernel tree onto
`chroot_native/mnt/linux` **without removing the previous one** — `deactivate`
does not unmount. After a day of build cycles it was stacked 16 deep.

The breakage is not the shape the error suggests. The `.output/Makefile`
overmount sits on whichever `/mnt/linux` layer was on top when it was created.
Later binds stack *on top of it*, so the path
`/mnt/linux/.output/Makefile` now resolves to the newest layer, where nothing is
mounted. It is still listed in `/proc/mounts` but is **unreachable by name**, so
`umount` returns 32 "not mounted" and `pmb.helpers.mount.umount_all` aborts the
whole command — during `build` (skipping the reindex) or during `install`.

The overmount is a child of the *bottom* layer, buried under everything else,
which is why `pmbootstrap shutdown` cannot recover on its own. Peel one layer at
a time, retrying the Makefile at each level as it becomes visible again:
`tkclean` does this.

```sh
awk -v p=<mnt> '$5==p' /proc/self/mountinfo    # what is actually there
```

Related: [[timestamps-cannot-prove-a-build-is-fresh]].
