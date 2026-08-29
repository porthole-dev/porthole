---
id: envkernel-disables-ccache
title: Every envkernel kernel build compiles from scratch, because envkernel disables ccache on purpose
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "helpers/envkernel.sh in the pmbootstrap checkout (EXTERNAL to this repo -- re-check it there) builds its `make` alias with `CCACHE_DISABLE=1` baked in, around line 281. Corroborated on the reference host 2026-08-29: cache_ccache_aarch64 holds 273 MB across 5230 files and `find -newermt '-30 days'` returns NOTHING, so the cache is populated but cold."
refutes: "kernel rebuilds are slow because of the chroot zap alone; enabling ccache in pmbootstrap's config would speed up envkernel builds; the ccache directory in the work dir means ccache is working; re-enabling ccache would make incremental kernel builds fast"
first-learned: 2026-08-29
---

**The question** — bring-up sessions spend six to ten minutes per kernel build.
How much of that is compilation that a cache should have removed?

**The answer** — all of it. envkernel builds its `make` alias like this:

```sh
cmd="$cmd pmbootstrap -q chroot --user --"
cmd="$cmd CCACHE_DISABLE=1"          # <- here
cmd="$cmd ARCH=$arch"
```

`CCACHE_DISABLE=1` is set on the command itself, per invocation. That beats any
environment variable an outer script exports, and it applies to **every rung**,
because every rung compiles through this alias.

The work directory's `cache_ccache_<arch>` is therefore a decoy. It exists, it
is 273 MB, it has 5230 files — and nothing in it has been touched in thirty
days. A cache directory is not a working cache, and its size is not evidence
that anything is hitting it.

**What this rules out** — that the wall clock is mostly the chroot zap.
`ph-build.sh` documents `PORTHOLE_LAX_BUILD=1` as skipping the buildroot zap
and calls that "most of the wall clock in the flashing rungs", which is true
of the *packaging* rungs. It does not touch the compile, and the compile is
uncached.

It also rules out fixing this through pmbootstrap's own `ccache` setting: that
governs package builds via `pmb/build/backend.py`, not the envkernel alias.

**MEASURED 2026-08-29, and the answer is that it does not matter much.** Two
`porthole build auto` runs on the taimen tree, host-side:

| run | wall | compile steps |
|---|---|---|
| nothing to rebuild | 44 s | 0 |
| one driver file touched (`hfi_venus.c` -> `venus-core.ko`) | 54 s | 6 |

So the fixed cost -- chroot init plus make's own dependency scan -- is about
40 s, and compiling one module is about 10 s of it. **The compile is not the
bottleneck**, and ccache could not have helped either run: `.output` lives in
the tree on the host and survives chroot recreation, so an incremental build
never repeats a compilation, and repeating compilations is the only thing
ccache accelerates.

Where it would still pay is a full rebuild -- `make clean`, a kernel version
move, or a header change touching thousands of files. Those are rare here.

**So the six-to-ten minute builds are not this.** They are the packaging rungs:
`pmbootstrap build` zapping the buildroot, plus `install` and `export`. That is
what `PORTHOLE_LAX_BUILD=1` addresses, and what picking the right rung avoids
entirely -- the same touched file routes to `mod` at ~40 s rather than `kernel`
at ~10 minutes.

**Re-checked 2026-08-29, evening, against the case that should have hit it.**
A chroot re-init forced a near-full kernel rebuild -- thousands of objects,
`net/`, `drivers/`, `fs/`, minutes of compiling -- and during it

```
find <workdir>/cache_ccache_aarch64 -type f -newermt '-2 hours' | wc -l
```

returned **0**. A full rebuild is precisely the case where ccache would pay,
and it was not touched. The `CCACHE_DISABLE=1` line is still at
`helpers/envkernel.sh:281`, in the host checkout and in the workspace image's
`/opt/pmbootstrap-src` alike.

**Still not established** Whether re-enabling it actually
helps here has NOT been measured. Three things have to be true and none were
checked, because the pmbootstrap work directory was in use by another agent at
the time:

- `ccache` must exist inside the native chroot
- `CCACHE_DIR` must resolve to the mounted `cache_ccache_<arch>`
- the compiler invocation must be one ccache can cache (`LLVM=1` is the default
  path here, and clang plus ccache has its own caveats)

There is presumably a reason upstream disabled it. Find that reason before
assuming this is free speed — a cache that returns a stale object for a kernel
is a debugging session nobody enjoys, and this repo already has
`brain/traps/` entries about stale build state presenting as mysterious
wrong-kernel bugs.

**How to check the claim cheaply**, once the work directory is free:

```sh
find <workdir>/cache_ccache_aarch64 -type f -newermt '-1 days' | wc -l   # 0 today
grep -n CCACHE_DISABLE <pmbootstrap>/helpers/envkernel.sh
```
