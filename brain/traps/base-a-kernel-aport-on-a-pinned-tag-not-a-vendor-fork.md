---
id: base-a-kernel-aport-on-a-pinned-tag-not-a-vendor-fork
title: Base a kernel aport on pristine kernel.org or a pinned tag — never an untagged vendor fork
scope: generic
subsystem: packaging
severity: technique
confidence: proven
evidence: survey of 426 pmaports kernel packages, 2026-08-08; taimen inherited a Nubia bring-up branch carrying a private AVB signing key and a tree-wide -O3 switch
first-learned: 2026-08-08
---

What pmaports actually does, measured across 426 aports rather than assumed:

- **22** pull pristine `cdn.kernel.org` tarballs; **37** pull from a
  `<soc>-mainline` fork — but every healthy qcom fork **pins a git tag** that
  tracks current mainline (`msm8953-mainline v7.0.9-r0`,
  `sdm845-mainline sdm845-7.1-rc1-r0`, `sc7280 v7.1.2-sc7280`).
- **Median patch count is about 4.** `sc7180` ships 6.18.38 with **zero**
  patches. `linux-google-nyan` is kernel.org 6.6.144 plus 6 numbered patches.
- Patches are numbered `format-patch` series (`0001-…`), one logical change per
  file — **not** a squashed `git diff` dump.

## Therefore

Base on **pristine kernel.org LTS**, carry your work as a numbered
`format-patch` series, and drive the patch count *down* over time by
upstreaming. A series that only grows is a fork wearing a patch series as a
disguise.

## Never pin an untagged commit from an unvetted fork

This is the expensive half. A vendor bring-up branch is not a base: it is
somebody else's unfinished work plus whatever they needed that day. Taimen was
based on a Nubia bring-up branch that carried a **private AVB signing key** and
a **tree-wide `-O3` switch** — neither noticed for months, both inherited by
every build, and one of them a genuine security problem to publish.

If you must start from a fork, read what you are inheriting first:

```sh
git log --oneline <upstream-tag>..<fork-head> | wc -l   # how much is theirs
git diff --stat <upstream-tag>..<fork-head>             # what it touches
git log -p <upstream-tag>..<fork-head> | grep -iE 'BEGIN (RSA|EC|OPENSSH|PRIVATE)'
```

A tree-wide compiler-flag change and a committed key are both greppable in
minutes and both cost far more than that once shipped.

Related: [[90-upstreaming]], [[commit-conventions]],
[[never-flash-a-tree-built-kernel-when-the-device-ships-from-an-aport]].
