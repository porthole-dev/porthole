---
id: the-workspace-loop-is-seconds-and-still-uncached
title: The workspace edit-build loop is 6-8 s, and ccache is still hit zero times
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: Reference host 2026-08-29, workspace container, taimen linux-ws on taimen-v7.2 (v7.2.2 + 178 patches), PMB_SUDO unset. porthole build (auto, preview) three times: cold tree after a 6.18->7.2 branch switch 14m42s / 3879 compile steps; no-op 6s / 0 steps; one .c touched (venus/hfi_venus.c) 7s / 4 steps, routed to the mod rung. ccache before and after the compiling run: cache_ccache_x86_64 23 bytes -> 23 bytes, cache_sccache 24 -> 24, container /root/.cache/ccache 0.0 GiB, and no cache_ccache_aarch64 directory exists at all. helpers/envkernel.sh:281 (in the pmbootstrap checkout, EXTERNAL to this repo -- re-check it there) still bakes CCACHE_DISABLE=1 into the make command.
refutes: a container build is slower than a host build; the workspace fixed cost is the 44 s the host findings measured; the compiler cache in the sandbox image makes kernel builds fast; ccache is what would cut the loop
first-learned: 2026-08-29
---

**The question** — is a second build fast? And is the compiler cache in the
sandbox image actually being hit?

**Fast, yes. Cached, no — and the two are unrelated.**

| run | wall | compile steps |
|---|---|---|
| cold tree (6.18 -> 7.2 branch switch, everything rebuilds) | **14m42s** | 3879 |
| no-op, nothing touched | **6 s** | 0 |
| one `.c` touched, one module out | **7 s** | 4 |

The host-side numbers in [[where-the-build-minutes-actually-go]] and
[[envkernel-activation-is-cheap-once-the-chroot-is-warm]] are 44 s for the
no-op and 54 s for one module. **The workspace is about seven times cheaper on
the fixed cost, not slower.** A container build being slow because it is a
container is the intuition to drop.

**ccache is hit zero times, in the workspace exactly as on the host.** Before
and after a run that provably compiled (`CC [M] hfi_venus.o`, 4 steps,
`venus-core.ko` relinked):

- `cache_ccache_x86_64` 23 bytes → 23 bytes
- `cache_sccache` 24 bytes → 24 bytes
- the container's own `/root/.cache/ccache` — 0.0 GiB
- `cache_ccache_aarch64` — **does not exist**, and never did, after a 3879-step
  aarch64 kernel compile

The mechanism is unchanged from [[envkernel-disables-ccache]]: `helpers/envkernel.sh`
line 281 puts `CCACHE_DISABLE=1` on the `make` command itself, which beats any
environment variable, and the workspace runs that same envkernel. The
compiler cache in the sandbox image is real and installed; nothing on the
kernel path reaches it.

**And it does not matter.** `.output` lives in the tree and survives, so an
incremental build never recompiles a file — and repeating a compilation is the
only thing ccache accelerates. The one run above that ccache could have helped
is the 14m42s full rebuild, which happens when you switch branches across a
kernel version, i.e. rarely. Chasing a cache to speed up a 7-second loop is
the wrong end of the problem.

**What this rules out** — that the workspace costs wall clock over the host
(it saves it); that ccache is the lever on the edit-build loop (the loop is 7 s
and uncached); that the sandbox image's compiler cache changes kernel build
times (it is not on that path); and that `cache_ccache_*` existing means
anything is hitting it.

**How it was established** — three `porthole build` preview runs against
`linux-ws`, with `du -sb` on every cache directory before and after the
compiling run, plus `ccache -s` inside the container. The compiling run is the
positive control: a null from a build that compiled nothing would prove
nothing about caching. Overturned by envkernel dropping `CCACHE_DISABLE=1`, or
by an `aarch64` cache directory appearing with recent mtimes.
