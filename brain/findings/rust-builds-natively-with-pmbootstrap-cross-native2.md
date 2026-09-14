---
id: rust-builds-natively-with-pmbootstrap-cross-native2
title: A bindgen Rust aport builds in minutes with cross-native2, once pmbootstrap sets cargo up for it
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "2026-09-14, porthole workspace, pmbootstrap 3.11.1 + branch build/rust-cross-native2 (carried in sandbox/patches), aport testing/obscura (gtk4-rs, libadwaita-rs, gstreamer-rs, libcamera-rs with bindgen; 213 crates). options=!pmb:crossdirect (QEMU only): 140 of 213 crates after 7 min, still compiling at 40 min. options=pmb:cross-native2: whole package, obscura-0.1.0-r1.apk 1137 KiB, in 2m35s; aarch64 PIE, interpreter /lib/ld-musl-aarch64.so.1, so: deps on libcamera.so.0.7, libgtk-4, libadwaita-1. chroot_native and chroot_buildroot_aarch64 both rust 1.98.1-r0 (48a229cea); native rustc lists aarch64-alpine-linux-musl."
refutes: "Rust packages can only be built with QEMU or crossdirect in pmbootstrap; bindgen needs LIBCLANG_PATH pointed at /native or pregenerated bindings; target.<triple>.rustflags (CARGO_TARGET_*_RUSTFLAGS) is enough to pass --sysroot under abuild; cross-native2 cannot work for Rust because Alpine ships no cross standard library"
first-learned: 2026-09-14
---

**The question** — can a Rust aport whose build script runs bindgen (every
libcamera-rs, and most `*-sys` crates that generate bindings) be built fast for
aarch64 on an x86 host, without hacks in the APKBUILD?

**The answer** — yes, with `options="pmb:cross-native2"`, in about 2.5 minutes
instead of the better part of an hour, once pmbootstrap exports four things
for cross-native2 (it already does the Go equivalent):

    CARGO_BUILD_TARGET=aarch64-alpine-linux-musl
    CARGO_TARGET_AARCH64_ALPINE_LINUX_MUSL_LINKER=aarch64-alpine-linux-musl-gcc
    RUSTFLAGS=--sysroot=/mnt/sysroot/usr -Clink-arg=--sysroot=/mnt/sysroot
    BINDGEN_EXTRA_CLANG_ARGS_aarch64_alpine_linux_musl=--target=... --sysroot=/mnt/sysroot

and stops installing every build dependency of a Rust aport into the native
chroot for cross-native2 (that crossdirect rule dragged libcamera-dev in as an
x86 package and set off a nested strict build, which a rootless workspace
cannot zap).

**Why each dead theory is dead**

- *QEMU or crossdirect only.* crossdirect builds build scripts as native
  binaries that then dlopen() the target chroot's libclang (RPATH
  /usr/lib/llvmNN/lib) and die with "unsupported relocation type 1026";
  QEMU-only works and is ~20x slower.
- *LIBCLANG_PATH at /native.* The x86 libclang then loads the aarch64
  libLLVM through its RPATH. Pregenerated bindings dodge the problem instead of
  building the way Alpine does.
- *target-scoped rustflags.* abuild's default.conf exports RUSTFLAGS, and cargo
  lets RUSTFLAGS replace `target.<triple>.rustflags`: rustc never saw the
  sysroot and every target crate failed "can't find crate for `core`".
  RUSTFLAGS itself is safe because with a build target set cargo passes it to
  target artifacts only; build scripts and proc-macros stay native.
- *no cross std.* The target standard library comes from the sysroot's own
  `rust` package (put `rust` in makedepends_host); it is the same toolchain
  release as the native one, the same match crossdirect already requires.

**How to apply** — split the aport into `makedepends_build` (cargo,
cargo-auditable, clang-libclang, meson, pkgconf, ...) and `makedepends_host`
(rust plus the target -dev packages), add `pmb:cross-native2` (and `!check`:
cross-built tests cannot run on the build machine), and make sure a meson
wrapper copies cargo's output from `target/$CARGO_BUILD_TARGET/release`. Until
pmbootstrap carries the change, the workspace image applies it; editing the
patch now rebuilds the image (the recipe hash covers the build context).
