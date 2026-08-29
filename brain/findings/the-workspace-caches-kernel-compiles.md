---
id: the-workspace-caches-kernel-compiles
title: The workspace caches kernel compiles now: 18% dearer the first time, 2.5x faster every repeat
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Reference host 2026-08-29/30, workspace container, taimen-v7.2 worktree. `touch include/linux/regulator/consumer.h` then `porthole build auto`, counters zeroed before each, same 643 compile steps every run: UNCACHED (clang symlinks removed, PORTHOLE_NO_CCACHE=1) 1m49s; COLD 2m09s / 298 misses of 321 cacheable; WARM 43s / 320 hits of 321; WARM after a real non-lax `pmbootstrap build device-google-taimen` deleted and rebuilt chroot_native, 54s / 317 hits of 317. Upstream rationale: pmbootstrap fe28a39f, 2022-06-14, MR 2189. Cache 23 bytes before the change, 54 MB after. Positive control on the small case: `hfi_venus.c` recompiled twice, 23/23 hits, 0 misses."
refutes: "kernel builds in the workspace are uncached; ccache cannot be enabled on the envkernel path; installing ccache in the sandbox image makes kernel builds fast; ccache would speed up the incremental loop"
first-learned: 2026-08-29
---

**The question** — [[envkernel-disables-ccache]] proved every kernel compile
here ran uncached and closed with three things that had to be true before
enabling it could help, none of them checked. Are they true, and does it pay?

**All three were false. Fixing all three costs 18% on a first build and
pays 2.5x on every repeat of it.**

| run | wall | compile steps | ccache |
|---|---|---|---|
| uncached — clang not behind ccache, upstream's behaviour | 1m49s | 643 | — |
| cold — ccache in the path, empty for these objects | 2m09s | 643 | 298 miss / 321 |
| warm — same header touched again | **43 s** | 643 | **320 hit / 321** |
| warm, after `chroot_native` was destroyed and rebuilt | **54 s** | 643 | **317 hit / 317** |

Same make work in all three: touching one widely-included header, so make
recompiles the identical 643 steps. The only variable is whether ccache had
seen them.

**The first two rows are the price**, and upstream was right that there is
one: a run that misses everywhere costs **20 s more** than no cache at all,
about 18%. That is ccache hashing and storing 298 objects it cannot serve. The
trade is therefore explicit — pay 18% the first time a set of objects is
compiled, take 1m49s -> 43 s every time it is compiled again. For bring-up,
where the same tree is rebuilt after every branch switch, chroot zap and
config change, that is a clear win. For a machine that builds a tree once and
never again it is a loss, and `PORTHOLE_NO_CCACHE=1` is the answer.

The last row is the one that matters and it was run, not reasoned: a real
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

**Why upstream disabled it — answered.** `fe28a39f`, Newbyte, 2022-06-14,
MR 2189, found by cloning pmbootstrap's history (the reference host's checkout
is depth-1, which is why an earlier pass recorded this as unknowable):

> Not extensively tested, but this shouldn't be necessary given that you get
> incremental builds with envkernel and may reduce build times.

Read it carefully, because it changes what this change is. It is a
**performance** judgement, not a correctness one — no stale-object hazard is
claimed, and the author says outright it was not extensively tested. Its
premise is *true for the case it names*: envkernel keeps `.output`, so an
incremental build never repeats a compilation and a cache is dead weight
there. Our own measurement agrees — the 6-8 s loop gains nothing.

The premise simply does not cover the case where make starts over: a chroot
re-init, a version move, a common header. Upstream did not consider it, and
the table above is that case. So enabling this is not overriding a safety
decision; it is extending an untested assumption to a case it never spoke to.

The residual hazard is still ours to carry, and it was never upstream's
argument: a stale object presenting as a mysterious wrong-kernel bug, which
this repo has paid for in other forms. Mitigations: ccache keys on
preprocessed source, compiler binary and flags, so a toolchain change misses
rather than lies; and `PORTHOLE_NO_CCACHE=1` turns it off without an image
rebuild.

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
