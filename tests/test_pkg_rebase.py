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


def E(text, mode="100644"):
    """One aport file, as `plan_rebase` wants it. Mode defaults to a plain
    regular file, which is what all but the fidelity cases care about."""
    return pkg.Entry(mode, text.encode() if isinstance(text, str) else text)


def git(repo, *args, check=True):
    return subprocess.run(("git", "-C", str(repo)) + args,
                          capture_output=True, text=True, check=check)


# ------------------------------------------------- plan_rebase, the pure --

def test_a_file_only_we_added_is_carried_forward():
    plan = pkg.plan_rebase({}, {"ours.patch": E("x")}, {})
    assert plan["ours.patch"]["verdict"] == "ours"
    assert plan["ours.patch"]["entry"].blob == b"x"


def test_a_file_upstream_added_since_we_forked_is_taken():
    plan = pkg.plan_rebase({}, {}, {"new.patch": E("u")})
    assert plan["new.patch"]["verdict"] == "upstream-new"
    assert plan["new.patch"]["entry"].blob == b"u"


def test_a_file_we_never_touched_follows_upstream():
    plan = pkg.plan_rebase({"a": E("1\n")}, {"a": E("1\n")}, {"a": E("2\n")})
    assert plan["a"]["verdict"] == "merged", plan["a"]
    assert plan["a"]["entry"].blob == b"2\n"


def test_a_file_only_we_changed_keeps_our_change():
    plan = pkg.plan_rebase({"a": E("1\n")}, {"a": E("mine\n")}, {"a": E("1\n")})
    assert plan["a"]["entry"].blob == b"mine\n", plan["a"]
    assert plan["a"]["verdict"] == "unchanged"


def test_non_overlapping_edits_on_both_sides_merge_cleanly():
    base = "top\nmiddle\nbottom\n"
    ours = "TOP\nmiddle\nbottom\n"
    theirs = "top\nmiddle\nBOTTOM\n"
    plan = pkg.plan_rebase({"a": E(base)}, {"a": E(ours)}, {"a": E(theirs)})
    assert plan["a"]["verdict"] == "merged", plan["a"]
    assert plan["a"]["entry"].blob == b"TOP\nmiddle\nBOTTOM\n", plan["a"]


def test_overlapping_edits_conflict_and_keep_both_sides_visible():
    """The positive control. Without a case that DOES conflict, every
    assertion above could pass with a merge that silently took one side."""
    plan = pkg.plan_rebase({"a": E("one\n")}, {"a": E("ours\n")},
                           {"a": E("theirs\n")})
    assert plan["a"]["verdict"] == "conflict", plan["a"]
    text = plan["a"]["entry"].text()
    for marker in ("<<<<<<< ours", "||||||| upstream at fork time",
                   ">>>>>>> upstream now"):
        assert marker in text, text
    assert "ours\n" in text and "theirs\n" in text, text


def test_a_file_we_deleted_stays_deleted():
    plan = pkg.plan_rebase({"gone": E("x")}, {}, {"gone": E("x")})
    assert plan["gone"]["verdict"] == "dropped"
    assert plan["gone"]["entry"] is None


def test_a_file_we_deleted_that_upstream_then_changed_says_so():
    plan = pkg.plan_rebase({"gone": E("x")}, {}, {"gone": E("rewritten")})
    assert plan["gone"]["verdict"] == "dropped"
    assert "upstream has since changed it" in plan["gone"]["note"]


def test_a_file_upstream_deleted_but_we_still_carry_is_kept_and_flagged():
    """mesa's real shape: upstream dropped llvm22-armhf.patch, our APKBUILD
    still lists it in source=. Dropping it silently would break the build."""
    plan = pkg.plan_rebase({"p": E("x")}, {"p": E("x")}, {})
    assert plan["p"]["verdict"] == "upstream-deleted"
    assert plan["p"]["entry"].blob == b"x"


def test_the_checksum_warning_fires_only_when_source_differs():
    same = {"APKBUILD": E('source="a.tar.gz\n\tone.patch\n"')}
    assert not pkg._needs_checksum(
        pkg.plan_rebase(same, same, same), same)

    ours = {"APKBUILD": E('source="a.tar.gz\n\tone.patch\n\tmine.patch\n"')}
    plan = pkg.plan_rebase(same, ours, same)
    assert pkg._needs_checksum(plan, same), plan["APKBUILD"]


def test_the_executable_bit_follows_the_same_three_way_rule_as_content():
    """A rebase that keeps our patch and drops our +x has not kept our patch.
    7 files inside pmaports aport directories are 100755, five of them in
    device-google-taimen."""
    base = {"run.sh": E("x", "100644")}
    ours = {"run.sh": E("x", "100755")}          # we made it executable
    plan = pkg.plan_rebase(base, ours, base)
    assert plan["run.sh"]["entry"].mode == "100755", plan["run.sh"]

    # ...and where WE did not touch the mode, upstream's wins.
    plan = pkg.plan_rebase(base, base, {"run.sh": E("x", "100755")})
    assert plan["run.sh"]["entry"].mode == "100755", plan["run.sh"]


def test_a_symlink_is_merged_by_identity_and_stays_a_symlink():
    """git stores a symlink's TARGET as blob content, so a reader that keeps
    only content turns `link -> run.sh` into a file holding "run.sh". 1084
    such files live in pmaports, across 382 aports."""
    base = {"l": E("old.sh", "120000")}
    theirs = {"l": E("new.sh", "120000")}
    plan = pkg.plan_rebase(base, base, theirs)
    assert plan["l"]["entry"] == pkg.Entry("120000", b"new.sh"), plan["l"]

    # Both sides retargeted it: not line-mergeable, so it is a conflict that
    # keeps ours rather than a merge that invents a path.
    plan = pkg.plan_rebase(base, {"l": E("mine.sh", "120000")}, theirs)
    assert plan["l"]["verdict"] == "conflict", plan["l"]
    assert plan["l"]["entry"].blob == b"mine.sh"


def test_a_binary_is_never_line_merged():
    """device-sony-taoshan/logo.rle, device-xiaomi-latte/MOK.cer. A three-way
    merge of a binary produces a binary-shaped thing that is not a binary,
    and does it without complaining."""
    blob = bytes(range(256))
    base = {"logo.rle": E(blob)}
    ours = {"logo.rle": E(blob + b"ours")}
    theirs = {"logo.rle": E(blob + b"theirs")}
    assert not base["logo.rle"].mergeable, "the fixture is not binary"
    plan = pkg.plan_rebase(base, ours, theirs)
    assert plan["logo.rle"]["verdict"] == "conflict", plan["logo.rle"]
    assert b"<<<<<<<" not in plan["logo.rle"]["entry"].blob
    assert plan["logo.rle"]["entry"].blob == blob + b"ours"


def test_crlf_survives_a_merge_untranslated():
    """Python's universal-newline translation, not git's doing: with
    text=True every CRLF in a patch came back LF -- a change to every line of
    a file neither side had touched."""
    base = {"p.patch": E(b"a\r\nb\r\n")}
    theirs = {"p.patch": E(b"a\r\nb\r\nc\r\n")}
    plan = pkg.plan_rebase(base, base, theirs)
    assert plan["p.patch"]["entry"].blob == b"a\r\nb\r\nc\r\n", \
        plan["p.patch"]["entry"].blob


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


def test_a_fork_point_before_a_rename_is_still_found():
    """Three of the four forks this port carries have histories crossing a
    `testing/ -> community/` promotion: phoc 103 commits without --follow and
    107 with, epiphany 96 and 108. Without it the refusal reads as "wrong
    version" when the truth is "the file moved".

    The fixture bumps the version BEFORE the rename on purpose. If the rename
    commit still carried the forked version, plain `git log` would find it and
    this would prove nothing -- which is exactly what the first draft did.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        up = tmp / "aports_upstream"
        subprocess.run(("git", "init", "-qb", "master", str(up)), check=True,
                       capture_output=True)
        git(up, "config", "user.email", "t@example.invalid")
        git(up, "config", "user.name", "t")
        apk = up / "testing" / "phoc" / "APKBUILD"
        apk.parent.mkdir(parents=True)

        apk.write_text("pkgname=phoc\npkgver=1.0\npkgrel=0\n")
        git(up, "add", "-A")
        git(up, "commit", "-qm", "phoc 1.0-r0, in testing")
        forked_at = git(up, "rev-parse", "HEAD").stdout.strip()

        apk.write_text("pkgname=phoc\npkgver=1.1\npkgrel=0\n")
        git(up, "add", "-A")
        git(up, "commit", "-qm", "phoc 1.1-r0, still in testing")

        (up / "community").mkdir()
        git(up, "mv", "testing/phoc", "community/phoc")
        git(up, "commit", "-qm", "promote phoc to community")

        (up / "community" / "phoc" / "APKBUILD").write_text(
            "pkgname=phoc\npkgver=2.0\npkgrel=0\n")
        git(up, "add", "-A")
        git(up, "commit", "-qm", "phoc 2.0-r0")

        # Positive control: the version really IS invisible without --follow,
        # so the assertion below is testing the flag and not the fixture.
        plain = git(up, "log", "--format=%H", "--", "community/phoc/APKBUILD")
        assert forked_at not in plain.stdout, \
            "the fork point is reachable without --follow; fixture proves nothing"

        ref, how = pkg.base_ref_for(up, "community/phoc",
                                    {"commit": "unknown", "forked": "1.0-r0"})
        assert ref == forked_at, (ref, forked_at, how)


def test_hitting_the_search_depth_says_so_rather_than_denying_it_exists():
    """"not in the last 400" is a reason to look further back; "not in all 12"
    is a reason to doubt the manifest. Reporting both as "not found" sends
    you to the wrong one."""
    with tempfile.TemporaryDirectory() as d:
        up, _shas = _upstream_with_history(pathlib.Path(d))
        _ref, shallow = pkg.base_ref_for(
            up, "main/mesa", {"commit": "unknown", "forked": "9.9-r9"})
        assert "anywhere" in shallow and "3 commits" in shallow, shallow

        deep = pkg.BASE_SEARCH_DEPTH
        try:
            pkg.BASE_SEARCH_DEPTH = 2
            _ref, capped = pkg.base_ref_for(
                up, "main/mesa", {"commit": "unknown", "forked": "9.9-r9"})
        finally:
            pkg.BASE_SEARCH_DEPTH = deep
        assert "depth limit" in capped, capped


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
        assert files["flat.patch"].blob == b"flat", files["flat.patch"]
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
            {"APKBUILD": {"entry": E("pkgname=mesa\npkgver=2.0\n")},
             "mine.patch": {"entry": E("ours")},
             "deleted.patch": {"entry": None}})

        assert (dest / "temp" / "mesa" / "mine.patch").read_text() == "ours"
        assert "pkgver=2.0" in (dest / "temp" / "mesa" / "APKBUILD").read_text()
        assert not (dest / "temp" / "mesa" / "deleted.patch").exists(), \
            "a file the plan drops must not be written"

        # OUTSIDE pmaports: a worktree inside it is an untracked directory
        # that `porthole sync` then refuses to sync past.
        assert pm not in dest.parents, dest
        assert _before_after(pm) == before, "pmaports moved under the rebase"


def test_leftover_scratch_worktrees_are_found_and_reported():
    """107 MB each, nothing prunes them, and `porthole disk` accounts for apk
    work dirs rather than worktrees -- so four forgotten rebases is 428 MB
    that nothing on the host mentions."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        pm = _fake_pmaports(tmp)
        ctx = _Ctx(tmp / "root", {"PORTHOLE_RUNDIR": str(tmp / "run")})
        assert pkg.existing_rebases(pm) == []

        pkg._write_rebase(ctx, pm, "porthole/rebase-mesa-2.0-r0",
                          pm / "temp" / "mesa", {"APKBUILD": {"entry": E("x")}})
        found = pkg.existing_rebases(pm)
        assert [w["branch"] for w in found] == ["porthole/rebase-mesa-2.0-r0"], found
        assert found[0]["bytes"] > 0, found

        # An unrelated worktree on somebody else's branch is not ours to name.
        subprocess.run(("git", "-C", str(pm), "worktree", "add", "-q", "-b",
                        "someone-elses-work", str(tmp / "theirs")), check=True,
                       capture_output=True)
        assert [w["branch"] for w in pkg.existing_rebases(pm)] == [
            "porthole/rebase-mesa-2.0-r0"]


def test_a_leftover_branch_stops_the_rebase_instead_of_overwriting_it():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        pm = _fake_pmaports(tmp)
        ctx = _Ctx(tmp / "root", {"PORTHOLE_RUNDIR": str(tmp / "run")})
        plan = {"APKBUILD": {"entry": E("x")}}
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


# ------------------------------------------------- the verb, end to end --
#
# THE GAP THIS CLOSES. Everything above tests `plan_rebase` and
# `_write_rebase` directly, and `_rebase` -- the function the CLI actually
# calls -- had no test at all. That is not theoretical: when files became
# `Entry`, a stale `apkbuild_fields(theirs.get("APKBUILD", ""))` survived
# every one of the 24 tests above and was caught only by running the verb
# against the real tree. A suite that covers the parts and not the wiring
# reports green on a command that raises TypeError on its first line of work.


class _VerbOut:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(text)

    def kv(self, key, value, width=0, note=""):
        self.lines.append(f"{key} {value} {note}")

    def blank(self):
        self.lines.append("")

    def warn(self, text, stream=None):
        self.lines.append(f"warning: {text}")

    def hint(self, text, note="", stream=None):
        self.lines.append(f"hint: {text}")

    def paint(self, text, _tone):
        return text


class _VerbArgs:
    def __init__(self, target, yes=False):
        self.target, self.yes, self.json = target, yes, False


class _VerbCtx:
    def __init__(self, root, cfg, args):
        self.root, self.cfg, self.args = str(root), cfg, args
        self.out, self.captured = _VerbOut(), None

    def emit(self, payload, render=None):
        self.captured = payload
        if render:
            render()
        return 0


def _whole_world(tmp: pathlib.Path):
    """A porthole root with a manifest, a pmaports with our fork, and an
    aports_upstream that has moved on. Everything `_rebase` reaches for."""
    up = tmp / "aports_upstream"
    subprocess.run(("git", "init", "-qb", "master", str(up)), check=True,
                   capture_output=True)
    git(up, "config", "user.email", "t@example.invalid")
    git(up, "config", "user.name", "t")
    ap = up / "main" / "mesa"
    ap.mkdir(parents=True)
    (ap / "APKBUILD").write_text(
        'pkgname=mesa\npkgver=1.0\npkgrel=0\nsource="a.tar.gz\n\tup.patch\n"\n')
    (ap / "up.patch").write_text("upstream's\n")
    git(up, "add", "-A")
    git(up, "commit", "-qm", "mesa 1.0-r0")
    forked_at = git(up, "rev-parse", "HEAD").stdout.strip()
    (ap / "APKBUILD").write_text(
        'pkgname=mesa\npkgver=2.0\npkgrel=0\nsource="a.tar.gz\n\tup.patch\n"\n')
    git(up, "add", "-A")
    git(up, "commit", "-qm", "mesa 2.0-r0")
    # `upstream_remote_ref` resolves to origin/<default>, and the verb reads
    # THAT rather than the checkout -- so the fixture needs a real remote or
    # it is testing the fallback instead of the path the verb takes.
    origin = tmp / "origin.git"
    subprocess.run(("git", "init", "-qb", "master", "--bare", str(origin)),
                   check=True)
    git(up, "remote", "add", "origin", str(origin))
    git(up, "push", "-q", "origin", "master")

    pm = tmp / "pmaports"
    ours = pm / "temp" / "mesa"
    ours.mkdir(parents=True)
    (ours / "APKBUILD").write_text(
        'pkgname=mesa\npkgver=1.0\npkgrel=14\n'
        'source="a.tar.gz\n\tup.patch\n\tmine.patch\n"\n')
    (ours / "up.patch").write_text("upstream's\n")
    (ours / "mine.patch").write_text("ours, and it must survive\n")
    (pm / "device").mkdir()
    subprocess.run(("git", "init", "-qb", "main", str(pm)), check=True,
                   capture_output=True)
    git(pm, "config", "user.email", "t@example.invalid")
    git(pm, "config", "user.name", "t")
    git(pm, "add", "-A")
    git(pm, "commit", "-qm", "seed")

    root = tmp / "root"
    (root / "profiles" / "test-device").mkdir(parents=True)
    (root / "profiles" / "test-device" / "aports.conf").write_text(
        "mesa\n"
        "  upstream: main/mesa\n"
        "  tier:     required\n"
        "  why:      the end-to-end fixture\n"
        "  forked:   1.0-r0\n"
        "  commit:   unknown\n")

    cfg = {"PORTHOLE_DEVICE": "test-device", "PORTHOLE_PMAPORTS": str(pm),
           "PORTHOLE_RUNDIR": str(tmp / "run")}
    return root, pm, up, cfg, forked_at


def test_the_verb_runs_end_to_end_and_carries_our_patch_onto_the_new_version():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        root, pm, _up, cfg, forked_at = _whole_world(tmp)
        args = _VerbArgs("mesa", yes=True)
        ctx = _VerbCtx(root, cfg, args)

        rc = pkg._rebase(ctx, args)

        payload = ctx.captured
        assert payload["onto"] == "2.0-r0", payload
        assert payload["base_ref"] == forked_at, payload
        assert payload["files"]["mine.patch"]["verdict"] == "ours", payload
        assert payload["needs_checksum"] is True, payload

        dest = pathlib.Path(payload["worktree"]) / "temp" / "mesa"
        assert (dest / "mine.patch").read_text() == \
            "ours, and it must survive\n"
        assert "pkgver=2.0" in (dest / "APKBUILD").read_text() or \
            "<<<<<<<" in (dest / "APKBUILD").read_text()
        # pmaports itself never moved.
        assert git(pm, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() \
            == "main"
        assert git(pm, "status", "--porcelain").stdout == ""
        assert rc in (0, 1), rc


def test_the_verb_refuses_an_aport_owned_outright():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        root, _pm, _up, cfg, _f = _whole_world(tmp)
        (root / "profiles" / "test-device" / "aports.conf").write_text(
            "ours-outright\n"
            "  upstream: (none -- ours, not a fork)\n"
            "  tier:     required\n"
            "  why:      fixture\n"
            "  forked:   n/a\n"
            "  commit:   n/a\n")
        args = _VerbArgs("ours-outright")
        try:
            pkg._rebase(_VerbCtx(root, cfg, args), args)
        except Exception as exc:                              # noqa: BLE001
            assert "owned outright" in str(exc), exc
        else:
            raise AssertionError("rebased something with no upstream")


def test_the_verb_refuses_a_name_that_is_not_in_the_manifest():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        root, _pm, _up, cfg, _f = _whole_world(tmp)
        args = _VerbArgs("not-carried")
        try:
            pkg._rebase(_VerbCtx(root, cfg, args), args)
        except Exception as exc:                              # noqa: BLE001
            assert "not in" in str(exc), exc
        else:
            raise AssertionError("rebased something not in the manifest")


def test_without_yes_the_verb_reports_and_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        root, pm, _up, cfg, _f = _whole_world(tmp)
        args = _VerbArgs("mesa", yes=False)
        ctx = _VerbCtx(root, cfg, args)
        pkg._rebase(ctx, args)

        assert ctx.captured["worktree"] == "", ctx.captured
        assert ctx.captured["files"], "it reported nothing at all"
        assert not (tmp / "run" / "rebase").exists(), "it wrote a worktree"
        assert pkg.existing_rebases(pm) == []


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
