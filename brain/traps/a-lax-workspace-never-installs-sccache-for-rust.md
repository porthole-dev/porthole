---
id: a-lax-workspace-never-installs-sccache-for-rust
title: A --lax workspace never installs sccache, so a QEMU-only Rust build dies in prepare
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-09-14 taimen, pmbootstrap 3.11.1 in the porthole workspace, aport testing/obscura (Rust, libcamera crate, options="net !pmb:crossdirect"). prepare() failed "could not execute process `/usr/bin/sccache rustc -vV` (never executed) No such file or directory"; chroot_buildroot_aarch64/usr/bin/sccache absent. pmb/build/backend.py sets RUSTC_WRAPPER=/usr/bin/sccache when ccache is on and cross != CROSSDIRECT; pmb/build/_package.py installs sccache only inside the one-time `pmb.build.init(buildchroot)` block (marker /tmp/pmb_chroot_build_init_done). With sccache installed into the buildroot the same build compiled crates past prepare.
first-learned: 2026-09-14
---

**In the workspace, a Rust aport built with `!pmb:crossdirect` fails before it
compiles anything unless sccache is put in the target buildroot first.
`porthole pkg build` now does that; a hand-run `pmbootstrap build` does not.**

pmbootstrap wraps rustc in sccache whenever ccache is enabled and the build is
not crossdirect, but it only installs sccache while initialising a buildroot
for the first time, and only when `rust` or `cargo` is a literal dependency.
Upstream a strict build zaps the buildroot before every build, so that
initialisation runs again and the two stay in step. The workspace has to
build `--lax` (see what-a-rootless-workspace-cannot-do), so the buildroot is
initialised once, by whatever was built first, and never again.

The error names sccache, but it arrives right after two rounds of bindgen
failures when you are moving a Rust crate off crossdirect, and it reads like
the same problem. It is not: the libclang fix already worked, the wrapper
binary is simply missing.

Also: when crossdirect breaks a Rust build because a build script needs the
TARGET's libraries (bindgen loading libclang), `!pmb:crossdirect` is the
pmaports answer. Do not point LIBCLANG_PATH at /native, and do not ship
pregenerated bindings: the x86 libclang then loads the aarch64 libLLVM
("unsupported relocation type 1026").
