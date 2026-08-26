---
id: two-config-copies-at-one-commit-break-every-module
title: The tree defconfig and the aport config can differ at the same commit, and modprobe pays for it
scope: generic
subsystem: kernel
severity: trap
confidence: proven
evidence: taimen, 2026-08-19 — tk-modcrc.py reported 86 of 155 shared symbols with mismatched CRCs at a single commit
first-learned: 2026-08-19
---

A kernel config normally lives in more than one place, and **the two builds read
different copies**:

| build | reads |
|---|---|
| envkernel / `make` in the tree | the tree defconfig, `arch/<arch>/configs/<name>_defconfig` |
| `pmbootstrap build` | the aport's `config-…` file |

Both can be committed, both can be current, and they can still disagree — at the
*same* commit, with nothing anywhere warning you.

## The symptom names no config at all

You build a module, push it, and `modprobe` refuses it:

```
foo: disagrees about version of symbol module_layout
```

`CONFIG_MODVERSIONS=y` hashes exported symbols into CRCs, and several ordinary
config differences change struct layouts. On taimen the drifting symbols were
`KPROBES`, `BPF_LSM`, `SECURITYFS`, `SECURITY_PATH`, `RD_ZSTD`,
`MODULE_COMPRESS*`, `DEBUG_INFO_*` and `NLS_ASCII` — none of which reads as
"this will stop every module loading".

The phone still boots and still answers ssh. It just comes up with no display,
no wifi and no audio, which reads as *the kernel change broke the device* rather
than *the modules are stale*.

## Check before pushing, not after

The check is free and runs on the host:

```sh
scp "$PHONE":/usr/lib/modules/<kver>/kernel/.../foo.ko /tmp/ref.ko
tools/tk-modcrc.py /tmp/ref.ko <tree>/.output/.../foo.ko    # exit 0 = it will load
```

Re-run it after any reflash — the reference module changes with the package.

## The fix is to build against the config the phone is running

```sh
cp <aport>/config-…                <tree>/arch/<arch>/configs/<name>_defconfig
TK_AGENT=<you> tools/tk-build.sh make <name>_defconfig
```

`porthole build` does this for you: `_ph_make` re-syncs the tree defconfig from
the aport before every build, precisely so the two cannot drift apart within a
cycle. That sync is load-bearing — it is also where syncing from the *wrong*
aport series costs a night, so keep `PORTHOLE_KERNEL_PKG` naming the series the
tree is actually on.

**Do not end a session with the copies differing.** `diff -q` is the whole check.

Related: [[olddefconfig-silently-drops-symbols]],
[[stale-dev-package-outranks-your-build]], [[prove-which-kernel-answered]],
[[shipped-configuration-is-not-running-configuration]].
