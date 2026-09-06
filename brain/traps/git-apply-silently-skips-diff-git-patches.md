---
id: git-apply-silently-skips-diff-git-patches
title: git apply silently skips 'diff --git' patches and exits 0, so a source tree ends up half-patched
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: taimen 2026-09-06. A --src webkit tree built for 5h18m was missing 8 of the aport's 18 patches. Reproduce: in a git work tree, `git apply -p1 <a patch whose header is "diff --git ... / index abc..def">` against paths git does not track -- it prints "Skipped patch 'X'." and returns 0.
first-learned: 2026-09-06
---

**Symptom** — a source tree you patched in a loop that reported success builds
into a binary missing some of the changes. Nothing failed. The loop checked
every exit code and every one was 0. The tree is patched *in part*: the fixes
you happened to test are there, the ones you did not are not, and the build is
a plausible-looking regression that costs whatever it costs to build.

On taimen this was 5h18m of webkit, and the resulting package had the CSS-filter
fix (so video freezes looked fixed) but not `frame-done-at-fence`,
`pipeline-compositor-frames-in-flight`, `cull-quads-outside-the-scissor-box` or
`video-flush-keeps-the-last-frame` -- the compositor patches. Measuring the
`WEBKIT_COMPOSITOR_MAX_FRAMES_IN_FLIGHT` knob on it would have set an
environment variable that no code reads and reported a clean null.

**Cause** — `git apply` run **inside a git work tree** treats a patch carrying a
`diff --git` header plus an `index <sha>..<sha>` line as a *git* patch, and
resolves its paths against the repository, not the current directory. When those
paths are not tracked here -- an upstream tarball unpacked into a gitignored
scratch directory is the usual case -- it declines the file, prints
`Skipped patch 'Source/…'.` on stdout, and **exits 0**. A plain `diff -u` /
`diff -ru` patch has no `index` line, takes the ordinary path, and applies. So a
mixed patch set splits cleanly in two and only half of it lands.

Two further ways to get a false pass on the same tree:

- `git apply --check -R` (the "is it already applied?" test) returns 0 for a
  patch that was never applied, for the same reason. It is not a presence test.
- `git apply --recount` widens it: it skipped 17 of 18 patches on a tree where
  the same patches without `--recount` applied 10.

**What to do** —

- **Use `patch(1)`, not `git apply`, on a tree that is not a git checkout.**
  That is what abuild does, and it is why the aport build of the same 18 patches
  was correct while the hand-patched tree was not. Note `patch` is often absent:
  it is not on a stock Fedora host and not in the porthole sandbox image, which
  is how the substitution gets made in the first place.
- **Treat `Skipped patch` as a failure.** An exit code is not enough:

      out=$(git apply -p1 -v "$p" 2>&1) || fail
      printf '%s' "$out" | grep -q '^Skipped patch' && fail

- **Verify by content, never by the applier's exit code.** Pick one distinctive
  added symbol per patch and grep the tree, or diff the built binary's symbol
  table against a known-good build -- four functions present in one and missing
  in the other is what exposed this one, in about a minute:

      nm -C --defined-only lib.debug | sed -n 's/^[0-9a-f]* [tT] //p' | sort -u

- An aport is the source of truth. A `--src` / out-of-tree copy is a convenience
  that can drift from it silently; [[the-taimen-v7-2-tree-was-ten-venus-patches-behind-its-own-aport-series]]
  is the same failure with a different mechanism.
