---
id: a-partial-chroot-umount-leaves-placeholder-device-nodes
title: A partial chroot umount in the workspace leaves /dev/urandom, /dev/zero and /dev/tty as empty root-only files -- and a 5.5 h build dies on the last step
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-09-01/02 taimen sandbox. webkit2gtk-6.0 2.52.6 compiled 9428/9429 objects in 5.5 h then g-ir-scanner's dump binary died SIGSEGV at 0xbbadbeef (WTF CRASH). QEMU_STRACE=1 on the saved dumper: openat("/dev/urandom") = EACCES. stat in both chroots: null/full/random are character devices 666, zero/urandom/tty are 'regular empty file 700 root'. log.txt pid 166488 at 16:58:44: umount dev/zero, dev/urandom, dev/tty, then dev/shm (fails, walk stops). Re-rbind of /dev over the chroot's /dev restored all three; the dumper then exited 0 and abuild `build rootpkg update_abuildrepo_index` resumed from the intact build dir. porthole-dev/porthole#33.
first-learned: 2026-09-02
---

The `mknod` shim gives a chroot the container's `/dev` by `mount --rbind`.
podman layers per-node binds inside that `/dev`, so pmbootstrap's shutdown
walks `dev/zero`, `dev/urandom`, `dev/tty`, ... as separate mounts and
unmounts them one by one until it hits `dev/shm` and dies (exit 32, see
[[what-a-rootless-workspace-cannot-do]] §5). Whatever it got through stays
gone: podman's placeholder file is what is left under the mountpoint, and
nothing re-checks -- `/dev/null` still passes `[ -c ]`, `create_device_nodes`
only runs `mknod` for a path that does not `exists()`, and the
`tmp/pmb_chroot_*_init_done` marker skips the major/minor verification.

A compile does not open `/dev/urandom`. Anything that seeds a PRNG at
startup does, and WebKit's `OSRandomSource` treats a failure as fatal --
so the failure lands hours in, on the last step, and reads as a qemu-user
or upstream bug. Before blaming either:

    stat -c '%F %a %t:%T' /pmb/chroot_*/dev/{null,zero,full,random,urandom,tty}

Every line must say `character special file 666`. Fix for the session:
`mount --rbind /dev /pmb/chroot_buildroot_aarch64/dev` (and native).
Resume rather than rebuild: the build dir survives, and
`abuild -d -D postmarketOS build rootpkg update_abuildrepo_index` in the
chroot as `pmos` with pmbootstrap's PATH picks up at the failed step.
