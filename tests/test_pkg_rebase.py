#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole pkg rebase` -- replaying a fork's delta onto newer upstream.

TWO LAYERS, DELIBERATELY.

`plan_rebase` is pure -- three {filename: text} mappings in, a plan out -- so
the merge cases are table-tested with no git repo and no pmaports. That is
where the logic lives, so that is where most of the tests are.

`base_ref_for` and the scratch worktree are NOT pure and are not mocked:
finding the fork-time commit is a fact about git history, and "the working
branch is untouched" is a fact about `git worktree`. Both get real
repositories, because a mock would let either pass while being wrong -- and
"it must never damage pmaports" is the property this whole verb is judged on.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_cmd_pkg as pkg                              # noqa: E402


def git(repo, *args, check=True):
    return subprocess.run(("git", "-C", str(repo)) + args,
                          capture_output=True, text=True, check=check)


# ------------------------------------------------- plan_rebase, the pure --

def test_a_file_only_we_added_is_carried_forward():
    plan = pkg.plan_rebase({}, {"ours.patch": "x"}, {})
    assert plan["ours.patch"]["verdict"] == "ours"
    assert plan["ours.patch"]["text"] == "x"


def test_a_file_upstream_added_since_we_forked_is_taken():
    plan = pkg.plan_rebase({}, {}, {"new.patch": "u"})
    assert plan["new.patch"]["verdict"] == "upstream-new"
    assert plan["new.patch"]["text"] == "u"


def test_a_file_we_never_touched_follows_upstream():
    plan = pkg.plan_rebase({"a": "1\n"}, {"a": "1\n"}, {"a": "2\n"})
    assert plan["a"]["verdict"] == "merged", plan["a"]
    assert plan["a"]["text"] == "2\n"


def test_a_file_only_we_changed_keeps_our_change():
    plan = pkg.plan_rebase({"a": "1\n"}, {"a": "mine\n"}, {"a": "1\n"})
    assert plan["a"]["text"] == "mine\n", plan["a"]
    assert plan["a"]["verdict"] == "unchanged"


def test_non_overlapping_edits_on_both_sides_merge_cleanly():
    base = "top\nmiddle\nbottom\n"
    ours = "TOP\nmiddle\nbottom\n"
    theirs = "top\nmiddle\nBOTTOM\n"
    plan = pkg.plan_rebase({"a": base}, {"a": ours}, {"a": theirs})
    assert plan["a"]["verdict"] == "merged", plan["a"]
    assert plan["a"]["text"] == "TOP\nmiddle\nBOTTOM\n", plan["a"]["text"]


def test_overlapping_edits_conflict_and_keep_both_sides_visible():
    """The positive control. Without a case that DOES conflict, every
    assertion above could pass with a merge that silently took one side."""
    plan = pkg.plan_rebase({"a": "one\n"}, {"a": "ours\n"}, {"a": "theirs\n"})
    assert plan["a"]["verdict"] == "conflict", plan["a"]
    text = plan["a"]["text"]
    for marker in ("<<<<<<< ours", "||||||| upstream at fork time",
                   ">>>>>>> upstream now"):
        assert marker in text, text
    assert "ours\n" in text and "theirs\n" in text, text


def test_a_file_we_deleted_stays_deleted():
    plan = pkg.plan_rebase({"gone": "x"}, {}, {"gone": "x"})
    assert plan["gone"]["verdict"] == "dropped"
    assert plan["gone"]["text"] is None


def test_a_file_we_deleted_that_upstream_then_changed_says_so():
    plan = pkg.plan_rebase({"gone": "x"}, {}, {"gone": "rewritten"})
    assert plan["gone"]["verdict"] == "dropped"
    assert "upstream has since changed it" in plan["gone"]["note"]


def test_a_file_upstream_deleted_but_we_still_carry_is_kept_and_flagged():
    """mesa's real shape: upstream dropped llvm22-armhf.patch, our APKBUILD
    still lists it in source=. Dropping it silently would break the build."""
    plan = pkg.plan_rebase({"p": "x"}, {"p": "x"}, {})
    assert plan["p"]["verdict"] == "upstream-deleted"
    assert plan["p"]["text"] == "x"


def test_the_checksum_warning_fires_only_when_source_differs():
    same = {"APKBUILD": 'source="a.tar.gz\n\tone.patch\n"'}
    assert not pkg._needs_checksum(
        pkg.plan_rebase(same, same, same), same)

    ours = {"APKBUILD": 'source="a.tar.gz\n\tone.patch\n\tmine.patch\n"'}
    plan = pkg.plan_rebase(same, ours, same)
    assert pkg._needs_checksum(plan, same), plan["APKBUILD"]


# ------------------------------------------------- base_ref_for, real git --

def _upstream_with_history(tmp: pathlib.Path):
    """An aports_upstream where main/mesa/APKBUILD was bumped three times."""
    up = tmp / "aports_upstream"
    subprocess.run(("git", "init", "-qb", "master", str(up)), check=True)
    git(up, "config", "user.email", "t@example.invalid")
    git(up, "config", "user.name", "t")
    apk = up / "main" / "mesa" / "APKBUILD"
    apk.parent.mkdir(parents=True)
    shas = {}
    for ver, rel in (("1.0", "0"), ("1.0", "1"), ("2.0", "0")):
        apk.write_text(f"pkgname=mesa\npkgver={ver}\npkgrel={rel}\n")
        git(up, "add", "-A")
        git(up, "commit", "-qm", f"mesa {ver}-r{rel}")
        shas[f"{ver}-r{rel}"] = git(up, "rev-parse", "HEAD").stdout.strip()
    return up, shas


def test_the_fork_time_commit_is_found_from_forked_when_commit_is_unknown():
    """Every entry that predates `pkg fork` recording provenance says
    `commit: unknown`, so this fallback is the ONLY way rebase works on any
    fork that exists today. mesa included."""
    with tempfile.TemporaryDirectory() as d:
        up, shas = _upstream_with_history(pathlib.Path(d))
        ref, how = pkg.base_ref_for(
            up, "main/mesa",
            {"commit": "unknown (backfilled 2026-09-09)", "forked": "1.0-r0"})
        assert ref == shas["1.0-r0"], (ref, shas)
        assert "walking" in how, how


def test_a_recorded_commit_wins_over_the_search():
    with tempfile.TemporaryDirectory() as d:
        up, shas = _upstream_with_history(pathlib.Path(d))
        ref, how = pkg.base_ref_for(
            up, "main/mesa", {"commit": shas["1.0-r1"], "forked": "1.0-r0"})
        assert ref == shas["1.0-r1"], ref
        assert "manifest" in how, how


def test_a_recorded_commit_that_is_not_in_the_repo_falls_back():
    """A sha from another clone must not become a hard failure -- the
    `forked:` version still identifies the commit here."""
    with tempfile.TemporaryDirectory() as d:
        up, shas = _upstream_with_history(pathlib.Path(d))
        ref, how = pkg.base_ref_for(
            up, "main/mesa",
            {"commit": "0" * 40, "forked": "1.0-r0"})
        assert ref == shas["1.0-r0"], ref
        assert "walking" in how, how


def test_an_unfindable_base_refuses_rather_than_guessing():
    """Rebasing onto the wrong base silently reclassifies upstream's own
    changes as our delta. Refusing is the only safe answer."""
    with tempfile.TemporaryDirectory() as d:
        up, _shas = _upstream_with_history(pathlib.Path(d))
        ref, why = pkg.base_ref_for(
            up, "main/mesa", {"commit": "unknown", "forked": "9.9-r9"})
        assert ref is None
        assert "9.9-r9" in why, why

        ref, why = pkg.base_ref_for(up, "main/mesa", {"commit": "unknown"})
        assert ref is None
        assert "forked:" in why, why


def test_the_tree_reader_returns_one_directory_and_flags_the_rest():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        up, shas = _upstream_with_history(tmp)
        nested = up / "main" / "mesa" / "sub" / "deep.patch"
        nested.parent.mkdir()
        nested.write_text("deep")
        (up / "main" / "mesa" / "flat.patch").write_text("flat")
        git(up, "add", "-A")
        git(up, "commit", "-qm", "add files")

        files = pkg._tree_at(up, "HEAD", "main/mesa")
        assert set(files) == {"APKBUILD", "flat.patch"}, files
        assert pkg._nested_at(up, "HEAD", "main/mesa") == [
            "main/mesa/sub/deep.patch"]


# ------------------------------------- the scratch worktree, real git --
#
# "It never touches the working branch" is the property this verb is judged
# on, and it is a fact about `git worktree`, not about our intentions. So it
# is asserted against a real repository, from the outside: branch, HEAD and
# `status --porcelain` before and after.


class _Ctx:
    def __init__(self, root, cfg):
        self.root, self.cfg = str(root), cfg


def _fake_pmaports(tmp: pathlib.Path):
    pm = tmp / "pmaports"
    (pm / "temp" / "mesa").mkdir(parents=True)
    (pm / "temp" / "mesa" / "APKBUILD").write_text("pkgname=mesa\n")
    subprocess.run(("git", "init", "-qb", "taimen-bringup", str(pm)), check=True)
    git(pm, "config", "user.email", "t@example.invalid")
    git(pm, "config", "user.name", "t")
    git(pm, "add", "-A")
    git(pm, "commit", "-qm", "seed")
    return pm


def _before_after(pm):
    return (git(pm, "rev-parse", "HEAD").stdout.strip(),
            git(pm, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
            git(pm, "status", "--porcelain").stdout)


def test_the_result_lands_outside_pmaports_and_the_working_branch_is_untouched():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        pm = _fake_pmaports(tmp)
        ctx = _Ctx(tmp / "root", {"PORTHOLE_RUNDIR": str(tmp / "run")})
        before = _before_after(pm)

        dest = pkg._write_rebase(
            ctx, pm, "porthole/rebase-mesa-2.0-r0", pm / "temp" / "mesa",
            {"APKBUILD": {"text": "pkgname=mesa\npkgver=2.0\n"},
             "mine.patch": {"text": "ours"},
             "deleted.patch": {"text": None}})

        assert (dest / "temp" / "mesa" / "mine.patch").read_text() == "ours"
        assert "pkgver=2.0" in (dest / "temp" / "mesa" / "APKBUILD").read_text()
        assert not (dest / "temp" / "mesa" / "deleted.patch").exists(), \
            "a file the plan drops must not be written"

        # OUTSIDE pmaports: a worktree inside it is an untracked directory
        # that `porthole sync` then refuses to sync past.
        assert pm not in dest.parents, dest
        assert _before_after(pm) == before, "pmaports moved under the rebase"


def test_a_leftover_branch_stops_the_rebase_instead_of_overwriting_it():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        pm = _fake_pmaports(tmp)
        ctx = _Ctx(tmp / "root", {"PORTHOLE_RUNDIR": str(tmp / "run")})
        plan = {"APKBUILD": {"text": "x"}}
        pkg._write_rebase(ctx, pm, "porthole/rebase-mesa-2.0-r0",
                          pm / "temp" / "mesa", plan)
        before = _before_after(pm)

        try:
            pkg._write_rebase(ctx, pm, "porthole/rebase-mesa-2.0-r0",
                              pm / "temp" / "mesa", plan)
        except Exception as exc:                              # noqa: BLE001
            assert "already exists" in str(exc), exc
        else:
            raise AssertionError("a second rebase overwrote the first")
        assert _before_after(pm) == before


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
