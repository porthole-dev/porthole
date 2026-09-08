---
id: crossdirect-hands-the-linker-to-qemu-on-purpose
title: Crossdirect hands every link step to qemu on purpose, not just compile
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "2026-08-30, porthole-sandbox container, gst-plugins-good 1.28.5-r50 forced rebuild (`pmbootstrap build --lax gst-plugins-good --arch aarch64 --force`), sampled with `podman exec porthole-sandbox ps -eo pid,ppid,stat,args=` every 5s for 37 samples over 18:02:59-18:06:12 (3m13s), during the meson link phase. Every sample: 0 running processes matched a bare/native compiler pattern; qemu-aarch64-static count ranged 87-158 per snapshot, all of the form `qemu-aarch64-static /usr/bin/cc -o <target> ... -flto=auto ...` (plugin .so links, tests/check link steps) plus `qemu-aarch64-static .../aarch64-alpine-linux-musl/bin/as ...ltrans*.ltrans.o` (LTO backend codegen spawned by that cc). Root cause read from source: `/pmb/cache_git/pmaports/cross/crossdirect/crossdirect.c` lines ~77-84 -- `if (!argv_has_arg(argc, argv, \"-c\")) { snprintf(newExecutable, ..., \"/usr/bin/%s\", executableName); execv(newExecutable, argv); }` with the comment 'linker is involved: just use qemu binary (to avoid broken cross-ld, pmaports#227)'. gst-plugins-good's build.ninja (`/pmb/chroot_buildroot_aarch64/home/pmos/build/src/gst-plugins-good-1.28.5/output/build.ninja`) records both `rule c_COMPILER` and `rule c_LINKER` as the bare name `cc $ARGS ...` -- no absolute path anywhere in ninja's own rules. `/pmb/chroot_buildroot_aarch64/native/usr/lib/crossdirect/aarch64/` holds `cc -> crossdirect-aarch64` (and gcc, g++, clang, clang++, cpp, plus HOSTSPEC-prefixed names), confirming crossdirect intercepts bare `cc` for both compile and link. Corroborating: `podman exec porthole-sandbox ps -eo pid,ppid,stat,args=` (one-shot, mid-build) showed 40 `[cc]`, 15 `[ccache]`, 14 `[clang++]`, 5 `[aarch64-alpine-]` zombies (state Z, ppid 1, argv already reclaimed) -- consistent with an earlier native-crossdirect compile phase that had already exited before sampling began; none were caught alive."
refutes: crossdirect is misconfigured or not installed for this build; the absolute path is baked into cmake's cache or ninja's generated rules; this is webkit- or cmake-specific; a stray PATH bug in pmbootstrap or the aport strips crossdirect out of PATH; the emulated share is proportional to how much source there is to compile
first-learned: 2026-08-30
---

**The question** — ~40% of compiler CPU during package builds is still
`qemu-aarch64`, even though crossdirect is confirmed installed and cmake (in
the taimen/webkit handoff) records its wrapper as `CMAKE_CXX_COMPILER`. The
emulated half was seen reaching the compiler by absolute path,
`qemu-aarch64-static /usr/bin/clang++`, which bypasses PATH and therefore
bypasses crossdirect. What performs that absolute-path invocation?

**The answer** — crossdirect itself. It is not misconfigured, not bypassed by
a stray PATH, and the absolute path is not written by cmake's cache or by
ninja's generated rules. `crossdirect.c` contains this, verbatim, as the very
first thing `main()` does before anything else:

    // linker is involved: just use qemu binary (to avoid broken cross-ld, pmaports#227)
    if (!argv_has_arg(argc, argv, "-c")) {
        snprintf(newExecutable, sizeof(newExecutable), "/usr/bin/%s", executableName);
        if (execv(newExecutable, argv) == -1) {
            ...
            fprintf(stderr, "NOTE: this is a foreign arch binary that would run with qemu (linker is involved).\n");
            exit_userfriendly();
        }
    }

`cc`/`gcc`/`g++`/`clang`/`clang++` in `/native/usr/lib/crossdirect/<arch>/`
are all symlinks to one `crossdirect-<arch>` binary built from this file.
When it is invoked *without* `-c` on the command line -- i.e. whenever the
compiler driver is being asked to link, not just compile a translation unit
-- it does not run the native cross-compiler at all. It builds
`/usr/bin/<executableName>` (an absolute path into the target-arch chroot)
and `execv()`s it directly. That binary is a foreign-arch ELF, so the kernel's
binfmt_misc hands it to `qemu-aarch64-static`, which is exactly the shape
seen in `ps`: `qemu-aarch64-static /usr/bin/cc ...`. Only the *compile*
path (`-c` present) reaches the native, ccache-fronted cross-compiler in
`NATIVE_BIN_DIR` (`/native/usr/lib/ccache/bin`). The comment names the
reason: a known "broken cross-ld" bug, tracked upstream as pmaports#227 --
this is a deliberate workaround, not an oversight.

This means the 40/60 split is not "some invocations bypass crossdirect" --
it is "crossdirect routes every *compile* natively and every *link*
through qemu, on purpose, by design." With `-flto=auto` in play (gst's
default C flags here), the "link" step is not just symbol resolution: the
linker re-invokes the compiler as its LTO backend, which spawns `as` to
assemble each `ltransN.ltrans.o` partition -- real compiler-grade codegen,
and all of it observed running under `qemu-aarch64-static` in every sample
taken. That is a plausible amplifier for why the emulated share of CPU is as
large as it is, though this note does not have per-process CPU-time
attribution to prove the *proportion* is caused by LTO specifically --
only that every live link-phase compiler process, LTO codegen included, ran
under qemu in every sample taken.

**What this rules out** —

- Not a misconfiguration: crossdirect is installed, its symlinks exist for
  `cc`/`gcc`/`g++`/`clang`/`clang++`/`cpp`, and `PATH` during the build
  (`PMB_CROSS=crossdirect PATH=/native/usr/lib/crossdirect/aarch64:...`, per
  `log.txt`) puts it ahead of the target chroot's own `/usr/bin`.
- Not cmake's or ninja's doing: gst-plugins-good's own `build.ninja` spells
  the compiler as the bare name `cc` in both `rule c_COMPILER` and
  `rule c_LINKER`. Neither rule hardcodes `/usr/bin/cc`. The absolute path
  is injected at *runtime*, inside crossdirect's own `main()`, after PATH
  resolution has already handed control to it.
- Not a build-system script invoking the compiler directly either --
  meson/ninja invoke `cc` exactly as they would for a native build; it is
  crossdirect's wrapper that re-execs the absolute path once it sees the
  invocation has no `-c`.

**What this does not establish (package-specific vs. general)** — the
mechanism is read directly from crossdirect's own source, which is shared
by every architecture and every aport that opts into
`options="pmb:crossdirect"` (or does not opt out); the `-c`-vs-link branch
has no gst-specific or meson-specific code path, so there is every reason to
expect it fires identically for webkit's cmake+ninja build, or any other
C/C++ package. But this spike did **not** run against webkit -- the webkit
build tree was gone before this task started (abuild wipes `$srcdir`, and a
later gst-plugins-good build had already replaced it), and starting a fresh
webkit build was explicitly out of scope (hours-long, reserved for a
protected overnight run). gst-plugins-good is C and meson; webkit is C++ and
cmake with Ruby/Perl codegen, and LTO flags, link-group structure, and the
number/size of link steps could differ enough to change the *proportion* of
CPU spent under qemu even if the *mechanism* (link routes through
`/usr/bin/<name>` + qemu) is identical. Observed here: zero native compiler
processes caught running in 37 consecutive samples, all during a link-heavy
phase (~30+ shared objects and ~100+ test binaries linked with `-flto=auto`).
Not observed: the compile phase running live (only its exited zombies), and
nothing at all about webkit's cmake-generated rules or clang++ link
invocations specifically.

**How it was established** — read `crossdirect.c` at
`/pmb/cache_git/pmaports/cross/crossdirect/crossdirect.c` inside the
sandbox (141 lines, matches the `crossdirect-aarch64` binary installed at
`/pmb/chroot_native/usr/lib/crossdirect/aarch64/crossdirect-aarch64` and
bind-mounted into the buildroot at
`/pmb/chroot_buildroot_aarch64/native/usr/lib/crossdirect/aarch64/`), cross-
checked against `ps -eo pid,ppid,stat,args=` samples taken every 5s during a
real forced rebuild, and against `build.ninja`'s two compiler rules for the
same package.

**What would overturn this** — running the same sampling against an actual
webkit2gtk-6.0 build (the protected overnight run, or a future spike) and
finding webkit's link steps resolve `clang++` to something *other* than
`/usr/bin/clang++` under qemu -- e.g. if webkit's aport sets
`options="!pmb:crossdirect"`, or its cmake cross-file names an absolute
compiler path that bypasses crossdirect's wrapper entirely (in which case
the cause would be webkit-specific, not this general mechanism), or if a
future crossdirect release drops the `-c`-only gate now that pmaports#227 is
resolved.

**Follow-up** — the detour's stated reason was tested against the toolchain
we actually have and did not reproduce:
[[the-qemu-link-detour-is-not-needed-on-this-toolchain]] links a working
aarch64 shared library and executable three different ways, natively,
including via the same hostspec-prefixed gcc driver crossdirect already uses
for the compile half. This note remains correct about *what* crossdirect
does; that one covers whether it still needs to.
