---
id: the-workspace-caches-kernel-compiles
title: The workspace caches kernel compiles now, and a repeated rebuild is 3x faster
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Reference host 2026-08-29, workspace container, taimen-v7.2 worktree. `touch include/linux/regulator/consumer.h` then `porthole build auto`, twice, counters zeroed before each: COLD 2m09s / 643 compile steps / 298 misses of 321 cacheable; WARM 43s / the same 643 steps / 320 hits of 321. Cache 23 bytes before the change, 54 MB after. Positive control on the small case: `hfi_venus.c` recompiled twice, 23/23 hits, 0 misses."
refutes: "kernel builds in the workspace are uncached; ccache cannot be enabled on the envkernel path; installing ccache in the sandbox image makes kernel builds fast; ccache would speed up the incremental loop"
first-learned: 2026-08-29
---

**The question** — [[envkernel-disables-ccache]] proved every kernel compile
here ran uncached and closed with three things that had to be true before
enabling it could help, none of them checked. Are they true, and does it pay?

**All three were false, and fixing all three pays 3x on a rebuild.**

| run | wall | compile steps | ccache |
|---|---|---|---|
| cold — cache empty for these objects | 2m09s | 643 | 298 miss / 321 |
| warm — same header touched again | **43 s** | 643 | **320 hit / 321** |
| warm, after `chroot_native` was destroyed and rebuilt | **54 s** | 643 | **317 hit / 317** |

Same make work in all three: touching one widely-included header, so make
recompiles the identical 643 steps. The only variable is whether ccache had
seen them.

The third row is the one that matters and it was run, not reasoned: a real
non-lax `pmbootstrap build device-google-taimen` deleted `chroot_native`,
pmbootstrap rebuilt it from scratch, `_ph_arm_ccache` re-armed the new chroot,
and every single compile hit a cache that had outlived the chroot. That is
exactly the "chroot re-init forced a near-full kernel rebuild" case, which used
to cost minutes. The extra 11 s over row two is envkernel's cold activation
into a fresh chroot, not compiling.

**What had to be fixed, and it was three things, not one:**

1. **ccache was on the wrong rootfs.** A compile runs as pmos inside
   `chroot_native`, and ccache was not installed there. The sandbox image's own
   `apk add ccache sccache` sat on the container rootfs, which a chroot cannot
   see — so it cached nothing and never could have. Removing `CCACHE_DISABLE=1`
   alone would have changed nothing at all.
2. **`CCACHE_DISABLE=1` on envkernel's make alias.** Still there upstream
   (3.11.1, `helpers/envkernel.sh:281`). `sandbox/Containerfile` rewrites that
   one line to `CCACHE_DIR=/mnt/pmbootstrap/ccache`, asserted so the image
   build fails if upstream ever moves it.
3. **clang was not reachable through ccache.** `/usr/lib/ccache/bin` is first
   on the chroot PATH, but Alpine's ccache ships masquerade symlinks for
   `gcc`/`cc`/`g++`/`c++` and none for clang — and this build is `LLVM=1`.

`_ph_arm_ccache` in `tools/ph-build.sh` does (1) and (3) on every activate,
guarded by a plain path test so the armed case costs nothing. The symlinks
it makes are RELATIVE: an absolute `/usr/bin/ccache` is dangling when the
guard stats it from outside the chroot, so the guard never fired and every
build paid a chroot round trip to re-arm what was already armed.

**Why on every activate and not once.** `zap_buildroots()` deletes
`chroot_native`, not only `chroot_buildroot*` (`pmb/chroot/zap.py:48-50`), so
every non-lax package build — every flashing rung — takes the chroot with it.
One-time setup would be disarmed by the next `porthole build kernel`. This is
also the mechanism behind "a chroot re-init forced a near-full rebuild".

**Where it does NOT help, and this is most of the loop.** The 6-8 s
incremental cycle is unchanged. `.output` lives in the tree and survives, so an
incremental build never repeats a compilation, and repeating a compilation is
the only thing ccache accelerates ([[the-workspace-loop-is-seconds-and-still-uncached]]).
The cases that pay are the ones where make starts over: a kernel version move
(the 14m42s 6.18 -> 7.2 rebuild), a common header, a chroot that got zapped, a
fresh workspace.

**The floor is not zero.** 26% of compiler calls are uncacheable — link,
MODPOST, BTF — and 43 s is that work plus make's dependency scan plus the ~6 s
fixed cost. A cache cannot take a rebuild below what is not a compile.

**What is still not known: why upstream disabled it.** The local pmbootstrap
checkout is depth-1 with no history to blame and the file gives no reason. The
hazard a cache carries is a stale object presenting as a mysterious
wrong-kernel bug, which this repo has paid for in other forms. Mitigations in
place: ccache keys on preprocessed source, compiler binary and flags, so a
toolchain change misses rather than lies; and `PORTHOLE_NO_CCACHE=1` turns the
whole thing off without an image rebuild.

**What would overturn this**: `ccache -s` showing misses on a repeat of an
identical build; a kernel that boots from an uncached build and not from a
cached one; upstream documenting a correctness reason for the line.

**How to check it cheaply**, from the host:

```sh
du -sh ~/.local/var/porthole-sandbox/cache_ccache_x86_64      # 23 bytes was the bug
podman exec porthole-sandbox pmbootstrap -q chroot --user -- \
  sh -c 'CCACHE_DIR=/mnt/pmbootstrap/ccache ccache -s'
podman exec porthole-sandbox grep -n CCACHE /opt/pmbootstrap-src/helpers/envkernel.sh
```
