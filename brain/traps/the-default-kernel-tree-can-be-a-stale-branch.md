---
id: the-default-kernel-tree-can-be-a-stale-branch
title: The default kernel tree can be parked on an old branch, and then every tool reports the wrong kernel version perfectly truthfully
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "google-taimen, 2026-09-19. The device had run 7.2.2 since aport r52 and was on #60/r59 that day, but <workdir>/linux was parked on the 6.18 topic branch wifi-disablekey-test with 19 uncommitted files. `porthole build --measure` correctly printed 'this tree is Linux 6.18, the device runs 7.2.2' and refused; `porthole doctor`'s kernel-tree row mentioned only shallowness and never the version. The 6.18-vs-7.2 confusion was re-derived across multiple sessions from exactly that, and the owner had to raise it."
first-learned: 2026-09-19
---

**The trap** — a kernel tree is a *working copy*, not a statement about the
port. `PORTHOLE_KERNEL_TREE` defaults to `<workdir>/linux`, and nothing keeps
that checkout on the product branch. Leave it on a topic branch from two kernel
versions ago -- which is the normal end state of any bisect or feature branch --
and every tool downstream reports that version, accurately, forever.

The failure is not that a tool lies. It is that "the tree is 6.18" and "the port
is 6.18" are different statements that look identical in output, and a reader
has no way to tell them apart from the line alone.

**What it looks like**

```
>> NOTE: profile says the product branch is taimen-v7.2
>>       this tree is on wifi-disablekey-test -- one of the two is stale
>> WARNING: this tree is Linux 6.18, the device runs 7.2.2
```

That NOTE is the tell, and it only appears once you try to build. Anyone who
read `doctor` or `brief` first never saw it.

**How to not fall in**

- The device is the authority on the device: `cat /proc/version`. The aport name
  is the authority on what ships (`PORTHOLE_KERNEL_PKG`). A tree is neither.
- Check a branch's version without checking it out:
  `git show <branch>:Makefile | head -3`.
- `KBUILD_BUILD_VERSION = pkgrel + 1`, so `#60` is aport r59. Reading the `#NN`
  as the pkgrel is off by one every time.

**Fixed, in three places, because one was not enough**

1. `PORTHOLE_KERNEL_TREE` pinned to a worktree that stays on the product
   branch (`git worktree add ../linux-<series> <product-branch>`), in
   `config.env` *and* `systemctl --user set-environment` -- the user manager
   holds a second, invisible copy of the config.
2. `doctor`'s kernel-tree row now compares the tree's `Makefile` version against
   the series in `PORTHOLE_KERNEL_PKG` and warns before it says anything else,
   naming both and how to fix it.
3. The stale checkout was **left exactly as found**. It was carrying 19
   uncommitted files that were not mine to discard, and adding a worktree costs
   a checkout while `git checkout` over someone's WIP costs their day.
