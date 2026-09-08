---
id: the-qemu-link-detour-is-not-needed-on-this-toolchain
title: The qemu link detour's "broken cross-ld" premise does not reproduce -- three native link paths all produce a working aarch64 binary
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "2026-09-08, porthole-sandbox container, chroot_buildroot_aarch64 with chroot_native bind-mounted at /native by hand (`mount --bind /pmb/chroot_native /pmb/chroot_buildroot_aarch64/native` plus the `/native/lib/ld-musl-x86_64.so.1` symlink pmbootstrap's `mount_native_into_foreign` creates). Every invocation used `env -i` with crossdirect's exact execve environment and nothing else -- `LD_PRELOAD= LD_LIBRARY_PATH=/native/lib:/native/usr/lib CCACHE_PATH=/native/usr/bin`, no PATH -- and crossdirect's exact argument prefix for the clang branch, `-target aarch64-alpine-linux-musl --sysroot=/`. Results: (A) `/native/usr/lib/ccache/bin/clang++ ... --ld-path=/native/usr/bin/ld.lld` -> `readelf -h` Machine: AArch64, binary runs under the target chroot and prints its output; (B) same driver with `--ld-path=/native/usr/bin/aarch64-alpine-linux-musl-ld` (the native cross GNU ld) -> AArch64, runs; (C) `/native/usr/lib/ccache/bin/aarch64-alpine-linux-musl-g++ --sysroot=/` with NO linker flag, NO -B and NO PATH -> AArch64, runs. Representative link, path (A): compiled two TUs `-fPIC -ffunction-sections`, linked `-shared -Wl,--gc-sections -Wl,--version-script -Wl,-soname,libh.so` -> `Type: DYN`, `Machine: AArch64`, SONAME `libh.so`, and `readelf --dyn-syms | grep -c unused_fn` = 0 proving the version script was honoured; then linked an executable against it with `-L/tmp -lh -Wl,-rpath,/tmp -Wl,--as-needed` -> NEEDED `libh.so` + `libc.musl-aarch64.so.1`, ran on target and printed `shared lib ok`. Sysroot resolution confirmed by `-Wl,--verbose`: `/lib/Scrt1.o`, `/lib/crti.o`, `/lib/gcc/aarch64-alpine-linux-musl/15.2.0/crtbeginS.o`, `/lib/libstdc++.so`, `/lib/crtn.o` -- all inside the aarch64 sysroot; `readelf -l` interpreter `/lib/ld-musl-aarch64.so.1`; NEEDED `libstdc++.so.6`, `libgcc_s.so.1`, `libc.musl-aarch64.so.1`. Source read at crossdirect 5.3.1-r1, `/pmb/cache_git/pmaports/cross/crossdirect/crossdirect.c`: `isClang` computed line 71, the unconditional qemu detour at lines 74-82, `NATIVE_BIN_DIR \"/native/usr/lib/ccache/bin\"` line 42, the env wipe lines 113-117. Toolchain: LLD 22.1.8, `/pmb/chroot_native/usr/bin/ld.lld` present, `/native/usr/lib/llvm22/bin/` contains no ld.lld."
refutes: the cross linker in this toolchain is broken; the detour is needed for clang; the detour is needed for gcc either; -B/native/usr/bin is a safe way to let the driver find lld; crossdirect is misconfigured; the user's 2h30m webkit floor is a compile-side cost that ccache should have removed
first-learned: 2026-09-08
---

**The question** — [[crossdirect-hands-the-linker-to-qemu-on-purpose]] proved
that crossdirect routes every link step through qemu deliberately, citing
"broken cross-ld, pmaports#227". Ten consecutive webkit2gtk-6.0 builds have
taken 2h30m each (the first, cold, took ~5h), a floor ccache cannot move
because ccache only ever sees the `-c` compile half. Is the detour's premise
still true on the toolchain we actually have?

**The answer** — no. Three different native link paths were tried under
crossdirect's own environment and argument shape. All three produced a working
aarch64 binary, and none of them ran qemu:

| path | linker reached | result |
|---|---|---|
| A: `clang++ --ld-path=/native/usr/bin/ld.lld` | LLD 22.1.8 | AArch64, runs |
| B: `clang++ --ld-path=.../aarch64-alpine-linux-musl-ld` | native cross GNU ld | AArch64, runs |
| C: `aarch64-alpine-linux-musl-g++`, no linker flag at all | its own default cross ld | AArch64, runs |

Path **C** is the one that settles it. The hostspec-prefixed gcc driver --
the exact binary crossdirect already execs for the compile half, at
`NATIVE_BIN_DIR "/" HOSTSPEC "-%s"` -- links a working aarch64 executable
with no linker flag, no `-B`, and no PATH. If the cross ld reachable from
that driver were broken, C could not have produced a running binary. Whatever
pmaports#227 described, it does not reproduce here.

**Where the "broken" impression comes from** — it is real, and it is a
`-B` artifact. Adding `-B/native/usr/bin` (the obvious way to let a driver
find `ld.lld` when the environment has no PATH) puts the *native, x86-64-only*
GNU `ld` ahead of the hostspec-prefixed cross one in the driver's search
order, and it fails exactly the way a broken cross linker would:

    /native/usr/bin/ld: unrecognised emulation mode: aarch64linux
    Supported emulations: elf_x86_64 elf32_x86_64 elf_i386 elf_iamcu i386pep i386pe

That message is a plain-`ld`-was-selected message, not a cross-ld-is-broken
message. `-B` is therefore the wrong fix, and this note records it as a
tested dead end rather than an untried idea.

**The second mechanism, which has to be handled at the same time** —
`-fuse-ld=lld` alone does *not* work under crossdirect's environment:

    clang++: error: invalid linker name in argument '-fuse-ld=lld'

`ld.lld` lives at `/native/usr/bin/ld.lld`, but the clang driver resolves
through `/native/usr/bin/clang++ -> ../lib/llvm22/bin/clang++`, so its own
directory is `/native/usr/lib/llvm22/bin/`, which contains no `ld.lld`. The
driver's remaining search is PATH, and crossdirect passes a literal
three-entry environment with no PATH in it -- the same env wipe recorded in
[[crossdirect-replaces-the-environment-on-exec]]. So a package's
`LDFLAGS="-fuse-ld=lld"` (which is what webkit2gtk-6.0 exports on every arch
except armv7) would silently fail to resolve if the detour were simply
deleted. `--ld-path=<absolute path>` is the form that works without
reintroducing an environment and without exposing the native `ld` that `-B`
exposes.

**Why this is the webkit number** — the compile half already runs natively
and already goes through ccache. That is why build 1 was ~5h and builds 2-10
were 2h30m flat: ccache removed almost all of the compile cost and could not
touch anything else. What remains is dominated by the link half, and the link
half is the half that runs under `qemu-aarch64-static`. A flat floor across
ten builds is the signature of a cost that caching does not reach.

**What this does NOT establish** — that removing the detour makes a webkit
build correct or that it is worth a specific amount of time. The links proven
here are a two-TU shared library plus an executable, exercising `-shared`,
`--gc-sections`, `--version-script`, `-soname`, `-L`/`-l`, `-rpath` and
`--as-needed`. WebKit links thousands of objects, uses linker scripts and
thin archives, and can exceed memory limits at link time; `-flto` was not
exercised at all here and is a genuinely different code path (the linker
re-invokes the compiler as its LTO backend -- see the LTO paragraph in
[[crossdirect-hands-the-linker-to-qemu-on-purpose]]). No timing claim is made
in this note: nothing was benchmarked, only proven to link and run. The
speedup is an expectation, not a measurement.

**What would overturn this** — a real webkit2gtk-6.0 build linking natively
and producing a broken or unloadable library where the qemu path produced a
good one; or a package whose link step depends on running a target-arch
binary mid-link (a plugin, a linker-invoked generator) that only works under
emulation; or finding that pmaports#227's actual failure needs a specific
input this two-TU test never presents.
