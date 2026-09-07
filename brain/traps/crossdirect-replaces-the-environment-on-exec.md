---
id: crossdirect-replaces-the-environment-on-exec
title: No CCACHE_ export reaches a cross compile, because crossdirect execs with a literal environment
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "Read from the source on the reference host 2026-09-07, pmaports/cross/crossdirect/crossdirect.c:113-135. The final call is `execve(newExecutable, newargv, env)` where `env` is the literal three-entry array `{\"LD_PRELOAD=\", \"LD_LIBRARY_PATH=/native/lib:/native/usr/lib\", \"CCACHE_PATH=/native/usr/bin\", NULL}`, built at line 114 and never appended to. The only other use of the caller's environment is a `getenv(\"LD_PRELOAD\")` at 118, which exists solely to abort with an explanation when a package runs the compiler under fakeroot during package(). Measured the same day: aarch64's cache sat at 2.8G of a 4.7G ceiling -- ccache's own 5 GB default -- which is raised by `ccache -M`, i.e. by the config FILE, and not by any variable."
first-learned: 2026-09-07
---

**Symptom** — you export `CCACHE_MAXSIZE`, `CCACHE_DIR` or `CCACHE_BASEDIR`
before a package build, the build runs normally, and the setting has no effect
at all. No error, no warning, nothing in the log. Setting it in
`pmbootstrap`'s environment, in porthole's config, or in the APKBUILD makes no
difference either.

## The mechanism

`crossdirect` is the shim pmbootstrap puts in front of the compiler in a cross
build. It does not add to the environment it was called with -- it *replaces*
it, with three entries:

```c
// crossdirect.c:114
char *env[] = { "LD_PRELOAD=",
    "LD_LIBRARY_PATH=/native/lib:/native/usr/lib",
    "CCACHE_PATH=/native/usr/bin",
    NULL };
...
// crossdirect.c:135
if (execve(newExecutable, newargv, env) == -1) {
```

`execve` with an explicit `env` argument discards the caller's environment
entirely. Those three strings are the **whole** environment the cross compiler
-- and therefore ccache -- is given. Nothing porthole exports, nothing
pmbootstrap exports, and nothing an APKBUILD exports is present by the time
ccache reads its settings.

## What does work

ccache's own config file, inside the cache directory, which is what
`ccache -M <size>` writes. It has to be run in the chroot that owns that
arch's cache:

    porthole build ccache --max 25G

That is why the verb exists rather than a documented environment variable, and
why `porthole sandbox up` sets the ceiling rather than exporting one.
`lib/porthole_cmd_build.py::_ccache` is the implementation.

## What this does not cover

The **link** half. A link never reaches this code at all: crossdirect
re-executes it as `/usr/bin/<name>`, a target-arch ELF that binfmt hands to
`qemu-<arch>-static`, several lines earlier and before this environment is
ever built. Deliberate, to dodge a broken cross-ld. That is a different note:
[[crossdirect-hands-the-linker-to-qemu-on-purpose]].

So the rule to carry: **for a cross build, a compiler setting is a file, not a
variable.** If a knob has no config file, there is no way to set it from
outside, and the answer is usually the APKBUILD's own flags rather than the
environment.

Related: [[two-pmbootstrap-builds-destroy-each-other]] for what else is true
of the buildroot these caches live in.
