---
id: envkernel-activation-is-cheap-once-the-chroot-is-warm
title: envkernel activation costs 0.8 s, not 14 s -- the 14 s is a one-off apk add
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Reference host 2026-08-29, taimen tree fully built, PMB_SUDO unset. `source helpers/envkernel.sh` timed in three separate fresh bash processes: 0.80 / 0.79 / 0.80 s, with `chroot_native/tmp/envkernel/llvm_setup_done` present. The same source measured 16.60 s earlier the same evening, with that flag ABSENT after a chroot re-init. Warm full-phase run: tkclean 0.17 s, envkernel 0.75 s, `make <defconfig>` 4.92 s, no-op `make -j` 5.61 s, `pmbootstrap build --envkernel` 14.66 s. Bind depth via /proc/mounts: 0 before activate, 1 after, and 0 again after `pmbootstrap build --envkernel` returns."
refutes: "every rung pays ~14 s to activate envkernel; caching the envkernel activation between builds is the big win; reusing the bind mount between builds would save 14 s; the bind survives a completed build; the compile is what makes builds slow"
first-learned: 2026-08-29
---

**The question** — [[where-the-build-minutes-actually-go]] says every rung pays
14.3 s to `source envkernel.sh`, and names reusing that activation as the next
big win. Is that right?

**No.** Activation costs **0.80 s** when the native chroot is warm. Measured in
three separate fresh processes, 0.80 / 0.79 / 0.80.

The 14 s is real but it is **not per build**. `initialize_chroot()` in
`envkernel.sh` installs about thirty packages (abuild, bison, flex, rust,
clang…) and then touches
`chroot_native/tmp/envkernel/<toolchain>_setup_done`. Every later activation
sees that flag and returns immediately:

```sh
flag="$chroot/tmp/envkernel/${toolchain_name}_setup_done"
[ -e "$flag" ] && return
```

So the number you measure depends entirely on whether that flag survived. Both
14 s readings on record were taken just after the native chroot had been
re-created — which clears `/tmp` and with it the flag. **Measure it twice: if
the second reading is under a second, the first one was the apk add.**

**What this kills.** The whole design of caching the activation across
builds — a state file, a replayed alias table, a token read back through the
chroot to prove the bind is still the right tree. That probe alone costs
**0.88 s**, which is *more* than activating from scratch. It would have been a
week of careful work on the script that flashes the phone, in exchange for
negative speed. It was written, measured and reverted once already for a
different reason; this is why it must not be written a third time.

It also kills the premise behind it: **the bind does not survive a build
anyway.** `pmbootstrap build --envkernel` returns with
`chroot_native/mnt/linux` at depth 0 — measured 1 → 0 across the call. Nothing
downstream could have reused it even if reuse were free. And envkernel does not
stack binds in the normal case: `mount_kernel_source()` unmounts first when
`$chroot/mnt/linux/Kbuild` exists, which it does in any kernel tree. Depth
stayed at 1 across three consecutive activations.

**Where the time actually goes, warm, per shell invocation:**

| phase | cost |
|---|---|
| `tkclean` | 0.17 s |
| `source envkernel.sh` | **0.75 s** |
| `make <defconfig>` | **4.92 s** |
| no-op `make -j` | 5.61 s |
| `pmbootstrap build --envkernel` | **14.66 s** |

Two of those are avoidable and neither is the mount handling. The defconfig
pass re-runs even when the config has not changed. The packaging step runs
inside `_ph_make`, which `porthole build auto` calls **purely to see what make
rebuilt** — and the router only reads `.ko`, `.dtb` and `Image.gz` under
`.output`, never the apk. See
[[the-auto-preview-builds-a-package-nobody-reads]].

**How it was established** — `date +%s.%N` around each phase, in a fresh
`bash` per run so nothing carried over, on a tree with nothing to rebuild.
The control for "is the flag what matters" is the pair of readings: 16.60 s
with the flag absent, 0.80 s with it present, same host, same tree, same hour.
