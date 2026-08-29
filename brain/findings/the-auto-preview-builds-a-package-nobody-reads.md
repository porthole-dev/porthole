---
id: the-auto-preview-builds-a-package-nobody-reads
title: porthole build auto spends 14.7 s making a _p apk its router never opens, and leaves it behind
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Reference host 2026-08-29. `pmbootstrap build --envkernel linux-postmarketos-qcom-msm8998-6.18` on a fully-built tree: 14.66 s and 14.87 s in two runs. The local repo's `_p` apk count went 1 -> 2 across one of them. `_auto` in lib/porthole_cmd_build.py calls `_ph_make` and then `_changed_artifacts`, which globs `.output` for `.ko`, `.dtb`, `Image.gz` only -- it never opens the apk. `_ph_make` has exactly three callers: `_auto` (measuring), `tkbuild`, `tkbuild-kernel`."
refutes: "the auto measure pass is free; auto without --yes changes nothing on disk; the _p apks in the local repo come from real builds"
first-learned: 2026-08-29
---

**The question** — `porthole build auto` without `--yes` is documented as
touching no device, and the skill tells every agent to run it habitually. What
does it actually do?

**It builds a kernel package and leaves it in the local repo.** `_ph_make` ends
with `pmbootstrap build --envkernel "$_PH_KPKG"`, which costs **14.66 s** on a
tree with nothing to rebuild — the single largest phase of the whole preview —
and writes a `_p` apk.

`_auto` calls `_ph_make` to answer one question: which files did make touch.
`_changed_artifacts` answers it by globbing `.output` for `.ko`, `.dtb` and
`Image.gz`. **It never reads the apk.** So the packaging is pure cost on that
path.

**And the cost is not only time.** `_ph_assert_no_devpkgs` and
`tkpurge-devpkgs` exist in the same file because an envkernel `_p` apk
outranks a release `-rNN` and gets installed instead of the package you meant
to ship — a class of bug this repo has been bitten by repeatedly and which
always presents as a mysterious wrong-kernel. The preview is manufacturing
exactly that. Measured: one `auto` measure pass took the `_p` count from 1 to
2.

**What this rules out** — that `auto` without `--yes` is a read-only
operation. It is a full package build with the device steps removed.

**The fix is a split, not a flag**: `_ph_measure` = activate + defconfig +
make + the existing artifact checks; `_ph_make` = `_ph_measure` + packaging,
unchanged for `tkbuild` and `tkbuild-kernel`, which do need the apk. `_auto`
calls `_ph_measure`.

**One consequence worth knowing before relying on the split**: packaging is
also what tears the `/mnt/linux` bind down — depth goes 1 → 0 across
`pmbootstrap build --envkernel`. A measure pass that skips packaging therefore
leaves the bind up, where a packaging one does not. Do not build anything on
that: activation is 0.75 s either way, see
[[envkernel-activation-is-cheap-once-the-chroot-is-warm]].
