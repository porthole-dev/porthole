---
id: a-tree-built-module-carries-btf-the-running-kernel-rejects
title: A tree-built module carries BTF the running kernel rejects, and modprobe blames a symlink loop
scope: generic
subsystem: kernel
severity: trap
confidence: proven
evidence: taimen 2026-08-31: patched mac80211.ko pushed, phone booted with no wlan0
first-learned: 2026-08-31
---


**Do not push a tree-built module without dropping its `.BTF` first.**
`tools/tk-push-module.sh` and `porthole build mod` both do this for you via
`tools/tk-strip-btf.py`; this note is why, and what it looks like when
something bypasses it.

`mod` did not, for the whole life of the verb. This note said the fix was
handled and it was -- in one of the two push paths. `tkmod()` pushed the raw
`.ko`, so the cheapest rung on the ladder, the one this very note tells you to
try first, walked straight into the trap: on 2026-08-31 a `porthole build mod`
on `ath10k_core` unloaded the old driver, failed to load the new one, and left
taimen with no wifi driver at all. Believing a fix is universal because one
caller has it is the shape of this mistake; `tests/test_strip_btf.py` now
asserts `tkmod` stages the module before its first `scp`.

A device that ships from an aport runs a kernel built in the aport chroot,
while `porthole build mod` builds the module from your tree. **MODVERSIONS is
happy with that** -- it compares exported symbol CRCs and those match, which is
exactly why the ladder says to try `mod` first. **BTF is a second gate nobody
mentions.** A module's `.BTF` references the *base kernel's* BTF by type id, so
a module built against a different vmlinux resolves those ids to the wrong
types or to a cycle:

```
BPF: Max chain length or cycle detected
failed to validate module [mac80211] BTF: -40
```

`btf_module_notify()` is a module notifier, and with
`CONFIG_MODULE_ALLOW_BTF_MISMATCH=n` a notifier returning an error **fails the
load**. -40 is `ELOOP`, and modprobe renders it as:

```
modprobe: ERROR: could not insert 'mac80211': Symbolic link loop
```

Nothing in that sentence names BTF, the module, the tree, or the kernel it was
built against. It reads like a broken symlink in `/lib/modules`.

**The cost, 2026-08-31:** a patched `mac80211.ko` was pushed and the phone
rebooted with **no `wlan0` at all** -- mac80211 never loaded, so `ath10k` had
nothing to attach to. The module on disk was demonstrably the right one
(`modinfo` listed the new parameter), which makes it look like a push problem
rather than a load problem.

**The tell, before you reboot:** `modinfo` shows your module, but
`/sys/module/<name>/parameters/<your new param>` does not exist after the
reboot. Always add a knob or check you can *see*, so "did my code run" has an
answer that is not the bug you are hunting.

**Why the fix is a 4-byte edit and not `strip`.** `llvm-strip --strip-debug`
would work, but `tk-push-module.sh` runs on the **host**, which has no
aarch64-capable strip -- and making the cheapest rung in the ladder depend on a
cross toolchain is a worse trade than the alternative. `tk-strip-btf.py`
instead sets the `.BTF` section header's `sh_name` to 0. The loader finds
sections by name, so an unnamed section is simply not there; nothing moves, no
offsets change, and it is idempotent and reversible.

Related: [[pushing-one-module-of-a-pair-corrupts-the-other]] is the other way a
`mod` push goes wrong while looking fine.
