---
id: the-boot-header-hash-does-not-cover-the-cmdline
title: The boot header hash does not cover the cmdline — which makes it the cheapest liveness test
scope: generic
subsystem: boot
severity: technique
confidence: proven
evidence: taimen docs/BUILD-RUNBOOK.md §4b; the Android boot image header's SHA1 is computed over (kernel, ramdisk, second) only
first-learned: 2026-08-19
---

The Android boot image header carries a SHA1 over `(kernel, ramdisk, second)`.
**The cmdline is not in it.** Patching the cmdline therefore produces an image
that still boots — no re-signing, no repack of the payload, no rebuild.

That turns an omission into the cheapest possible instrument: you can ask "did
this kernel reach `kernel_init`?" in seconds, without compiling anything.

```sh
tools/bootimg-cmdline.py patch boot.img -o /tmp/probe.img \
    --add rdinit=/nonexistent --add init=/nonexistent
fastboot boot /tmp/probe.img
```

Read the result as:

| observed | means |
|---|---|
| warm reboot in ~10 s (`panic=10`) | the kernel reached `kernel_init` and panicked on the missing init — **it is alive** |
| dark forever | it never got there; the failure is earlier than userspace |

With an initramfs, `rdinit=` is the one that decides; `init=` alone is resolved
too late to be the probe you meant.

A **warm** reboot also preserves ramoops, so `/sys/fs/pstore/` holds the log
afterwards. A power-button reset destroys it — so let the panic time out rather
than reaching for the button, or you throw away the evidence you just paid for.

## Why this belongs in the toolbox rather than a runbook

It answers the question that blocks every other question during early bring-up —
*is the kernel running at all* — and it answers it without a build. Reach for it
before instrumenting anything, and before concluding a boot failed at all:
[[wait-long-enough-before-calling-a-boot-failed]], [[never-judge-a-boot-by-the-screen]].

Related: [[prove-which-kernel-answered]], [[a-hard-hang-writes-nothing-to-disk]].
