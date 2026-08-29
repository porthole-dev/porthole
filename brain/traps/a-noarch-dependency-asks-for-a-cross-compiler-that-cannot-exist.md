---
id: a-noarch-dependency-asks-for-a-cross-compiler-that-cannot-exist
title: A noarch or all dependency makes pmbootstrap ask for gcc-<native>, and every fresh workspace dies on its first packaging rung
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "Reference host 2026-08-30, workspace container, pmbootstrap 3.11.1. `pmbootstrap build --lax device-google-taimen` queues 5 packages and dies on the first with `apk add crossdirect g++-x86_64 gcc-x86_64 abuild ccache-cross-symlinks` -> `unable to select packages: gcc-x86_64 (no such package)`. Reproduced on pinephone-callaudiod (arch=noarch) and rmtfs (arch=all); sns-reg (armv7 aarch64), soc-qcom-msm8998 and device-google-taimen (aarch64) are unaffected. `pmbootstrap build --arch aarch64 <pkg>` asks for gcc-aarch64 instead, which resolves to 15.2.0-r9 from the binary repo, and builds. After both offenders were rebuilt that way the remaining queue completed and device-google-taimen-1-r34.apk appeared in the workspace repo."
first-learned: 2026-08-30
---

**What it looks like** — the first packaging rung in a new workspace, dead
before it builds anything:

```
=> (1/5) edge/pinephone-callaudiod: Installing dependencies
(native) install ccache-cross-symlinks abuild gcc-x86_64 g++-x86_64
ERROR: unable to select packages:
  gcc-x86_64 (no such package)
```

`gcc-x86_64` is a cross compiler **to** x86_64. On an x86_64 host that package
does not exist and never will, because nobody cross-compiles x86_64 from
x86_64. The message names a missing package; the bug is that it was asked for.

**Why** — a dependency whose APKBUILD arch resolves to the NATIVE arch
(`arch="noarch"` or `arch="all"`) inside a build for a FOREIGN device arch gets
two archs that disagree:

- its cross mode is computed against the DEVICE arch, so
  `pmb/build/autodetect.py:52` sees emulation is required and returns
  `CROSS_NATIVE2` (noarch) or `CROSSDIRECT` (all) -- cross **enabled**
- its own `pkg_arch` resolves to the NATIVE arch, because that is where an
  arch-independent package builds

`pmb/build/_package.py:623` then calls `init_compiler(cross, pkg_arch)` and
`pmb/build/init.py:119` asks for `gcc-$pkg_arch`.

**Why it looks like bad luck** — it only fires when such a package actually
needs BUILDING. A machine that has built it once has it in its local repo
forever, which is why the reference HOST never saw this (its
`packages/edge/x86_64` already held `pinephone-callaudiod`) and why every
fresh workspace does. It also stayed hidden because `porthole build auto` and
`mod` go through envkernel and never call `pmbootstrap build` at all -- a
workspace can compile kernels happily for days and still have never run this.

**The fix, which is upstream's own logic** — name the device arch and the two
archs agree again:

```sh
pmbootstrap build --arch "$PORTHOLE_ARCH" <the package it named>
```

It then asks for `gcc-aarch64`, which exists, and builds. Since 2026-08-30
`_ph_pmb_build` in `tools/ph-build.sh` does this automatically: it detects the
`gcc-<native>` signature, reads the offending package out of the progress
line, rebuilds it with the device arch and retries the original, bounded at
eight passes because a queue can hold several.

**Do not "fix" it by adding the arch to the aport.** The package genuinely is
arch-independent; the disagreement is in how pmbootstrap pairs cross mode with
pkg_arch, and it belongs upstream.

**What would overturn this** — a pmbootstrap release where a noarch dependency
of a foreign-arch build installs `gcc-<device arch>`; at that point
`_ph_pmb_build`'s recovery never fires and can be deleted.
