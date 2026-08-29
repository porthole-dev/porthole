---
id: where-the-build-minutes-actually-go
title: Every rung pays ~14s to activate envkernel, and that dwarfs the compile
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Timed on the reference host 2026-08-29 against the taimen tree, PMB_SUDO unset. `source helpers/envkernel.sh` 14338 ms; no-op `make` 5395 ms; `pmbootstrap -q chroot -- true` 635 ms cold and ~880 ms warm; `tkclean` 4 ms. Two `porthole build auto` runs end to end: 44 s with nothing to rebuild, 54 s rebuilding one module (6 compile steps)."
refutes: "the compile is what makes builds slow; ccache would make incremental kernel builds fast; the chroot init is the fixed cost; reusing the envkernel bind between builds is a cheap win"
first-learned: 2026-08-29
---

**The question** — a bring-up session spends its life waiting on `porthole
build`. Where does the time actually go?

**The answer — not where it looks.** Measured, per invocation:

| step | cost |
|---|---|
| `source helpers/envkernel.sh` | **14.3 s** |
| no-op `make` (tree fully built) | 5.4 s |
| `pmbootstrap -q chroot -- true` | 0.6 s cold, ~0.9 s warm |
| `tkclean` (nothing stacked) | 0.004 s |

End to end, `porthole build auto` is 44 s with nothing to rebuild and 54 s
rebuilding a single module. So **compiling one module costs about 10 s and
getting ready to compile costs about 40 s.**

**What this rules out.** The chroot init is not the fixed cost -- it is under a
second. And ccache cannot help either number: `.output` lives in the tree on
the host and survives chroot recreation, so an incremental build never repeats
a compilation, and repeating compilations is the only thing ccache accelerates.
See [[envkernel-disables-ccache]].

**The obvious optimisation does not work, and this is the useful part.**
envkernel's 14.3 s is spent binding the tree into the chroot, so reusing an
existing bind should skip it -- and `make` driven from a cached copy of
envkernel's own alias measured 5.2 s against 5.4 s, i.e. identical. But the
bind does **not** survive a completed `porthole build`:

- `ph-build.sh` calls `tkclean` at the start of every `_ph_make`, which
  deliberately unstacks the bind, because stacking them makes pmbootstrap abort
  on the shadowed `.output/Makefile` overmount.
- Checking for it afterwards from the host is meaningless anyway: the bind
  lives in pmbootstrap's mount namespace, so the host path
  `<workdir>/chroot_native/mnt/linux` shows nothing even while the chroot sees
  it. The only honest check is `pmbootstrap -q chroot -- test -f
  /mnt/linux/Makefile`, which costs 0.85 s.

An attempt to cache and reuse was written, measured, and **reverted**: with
`tkclean` tearing the bind down at the start of each build, the fast path can
never fire, and a dead branch in a device-critical build script is worse than
none. Making it fire means restructuring how the mount is managed so that binds
do not stack -- which is a real change to that script, not a shortcut.

One more trap found on the way, worth its own line: `.output` is created by the
build INSIDE the chroot and is owned by the chroot's build uid, so **the host
user cannot write into it**. A cache file placed there fails silently if the
write error is swallowed.

**So the ways to make a build faster today, in order:** run the rung the change
actually needs (`porthole build` measures and picks -- one module is ~40 s
against `kernel`'s ~10 min), and use `PORTHOLE_LAX_BUILD=1` on the packaging
rungs. The compile is not the problem.
