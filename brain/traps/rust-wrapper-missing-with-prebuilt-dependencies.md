---
id: rust-wrapper-missing-with-prebuilt-dependencies
title: Rust wrapper missing with prebuilt dependencies
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: https://github.com/porthole-dev/pmbootstrap/commit/276919b37b355fafa2773e23aca2573595708c33; https://github.com/porthole-dev/pmaports/actions/runs/36642456650/job/109657801335; https://github.com/porthole-dev/pmaports/actions/runs/36661078089/job/109716081284
first-learned: 2026-09-30
---

**Symptom** — Cargo fails in `prepare()` with `could not execute process
/usr/bin/sccache rustc -vV` and `No such file or directory`. C dependencies
have already built successfully in the same native chroot.

**Cause** — pmbootstrap enables `RUSTC_WRAPPER` when compiler caching is on.
Installing sccache only during the chroot's first initialization misses Rust
packages reached after a C package. Detecting Rust from the source-build queue
also misses compiler dependencies already supplied as binary packages.

**What to do** — Check the current APKBUILD's declared build dependencies.
Install sccache when caching is enabled and those dependencies require Rust,
Cargo or cargo-auditable, even if the chroot is already initialized and the
source dependency queue is empty. Keep the chroot-local server socket.

**Control** — `test/build/test_native_sccache.py` executes the real setup block
with initialization returning false, Cargo declared, and an empty source queue.
The queue-based implementation fails; the recipe-based implementation passes.
The failing and successful Obscura APK jobs above provide the execution check.
