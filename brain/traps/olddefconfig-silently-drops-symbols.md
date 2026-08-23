---
id: olddefconfig-silently-drops-symbols
title: olddefconfig silently drops symbols whose dependencies are unmet
scope: generic
subsystem: kernel
severity: trap
confidence: proven
evidence: taimen AGENTS.md §1.1, the "QFPROM-class trap"
first-learned: 2026-08-08
---

You add a symbol to the defconfig. `olddefconfig` runs clean. The feature is
simply missing at runtime, with no error anywhere.

`olddefconfig` resolves unmet dependencies by dropping the symbol. It does not
tell you. The file is what you **asked for**; `.output/.config` is what you
**got**, and they are different documents.

```sh
# after any config change -- diff what you asked for against what you got
grep -E '^CONFIG_(A|B|C)=' .output/.config
```

Keep an explicit list of the symbols that must survive and grep for all of them
after every config regeneration. On taimen this is a 13-symbol sanity grep in
the build runbook, and it exists because a missing symbol cost a whole boot
hunt.

**Every config edit must exist in all of its copies, in the same commit.** A
defconfig in the tree and a config in the aport that drift apart produce a build
that matches neither.

Related: [[shipped-configuration-is-not-running-configuration]],
[[a-null-from-an-unexecuted-path-is-not-a-refutation]].
