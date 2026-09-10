#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole sync` -- report, push, fast-forward, and refuse.

REAL GIT, NOT A MOCK. Every interesting case here is a fact about what git
does, not about what porthole says to it: whether `merge --ff-only` refuses a
divergence, whether `push` reports a rejection, whether `@{upstream}` exists.
A mocked subprocess would let all four assertions pass while the command was
wrong, which is the failure `test_pkg_drift.py` was written against one plan
earlier. So each case builds a bare "remote" and clones off it.
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
import porthole_cmd_sync as sync                            # noqa: E402
from porthole_cli import EX_FAIL, EX_OK, EX_USAGE, Bail      # noqa: E402


def git(repo, *args):
    return subprocess.run(("git", "-C", str(repo)) + args,
                          capture_output=True, text=True, check=True)


def commit(repo, name, text="x"):
    (pathlib.Path(repo) / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", f"add {name}")


def make_clone(tmp: pathlib.Path, name: str):
    """A working clone with one commit, and the bare remote it tracks.

    Built init-then-push rather than clone-from-empty: cloning an empty bare
    repo works but prints "you appear to have cloned an empty repository" to
    stderr for every repo in every case, and a suite that always prints a
    warning is one where a real warning has nowhere to show up.
    """
    bare, work = tmp / f"{name}.git", tmp / name
    work.mkdir(parents=True)
    subprocess.run(("git", "init", "-qb", "main", "--bare", str(bare)), check=True)
    subprocess.run(("git", "init", "-qb", "main", str(work)), check=True)
    git(work, "config", "user.email", "t@example.invalid")
    git(work, "config", "user.name", "t")
    if name == "pmaports":
        # porthole_pmaports.find_pmaports accepts a candidate only if it has a
        # device/ directory. Without this the fake is skipped and resolution
        # falls through to the REAL checkout on the host running the test --
        # which is how the first run of this suite reported the tester's own
        # pmaports, 8 commits ahead, as the fixture.
        (work / "device").mkdir()
        (work / "device" / ".keep").write_text("")
        git(work, "add", "device")
    commit(work, "seed")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "-u", "origin", "main")
    return bare, work


# --------------------------------------------------------------- harness --

class _Out:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(text)

    def kv(self, key, value, width=0, note=""):
        self.lines.append(f"{key} {value} {note}")

    def blank(self):
        self.lines.append("")

    def hint(self, text, note="", stream=None):
        self.lines.append(f"hint: {text}")

    def warn(self, text, stream=None):
        self.lines.append(f"warning: {text}")


class _Args:
    def __init__(self, action=None, yes=False):
        self.action, self.yes, self.json = action, yes, False


class _Ctx:
    def __init__(self, root, cfg, args):
        self.root, self.cfg, self.args = root, cfg, args
        self.out, self.captured = _Out(), None

    def emit(self, payload, render=None):
        self.captured = payload
        if render:
            render()
        return EX_OK


def scenario(tmp: pathlib.Path, **cfg_extra):
    """All three repos as real clones, wired so resolve_target finds them.

    workdir and porthole come straight from config keys. pmaports is resolved
    by porthole_pmaports, and PORTHOLE_PMAPORTS is the key it honours first.
    """
    made = {name: make_clone(tmp, name)
            for name in ("workdir", "porthole", "pmaports")}
    cfg = {"PORTHOLE_WORKDIR": str(made["workdir"][1]),
           "PORTHOLE_PMAPORTS": str(made["pmaports"][1]),
           "PORTHOLE_DEVICE": "test-device"}
    cfg.update(cfg_extra)
    return made, cfg


def run(tmp, action=None, yes=False, **cfg_extra):
    made, cfg = scenario(tmp, **cfg_extra)
    args = _Args(action, yes)
    # ctx.root IS the porthole repo, which is what resolve_target("porthole")
    # returns -- so the clone named "porthole" has to be the root.
    ctx = _Ctx(str(made["porthole"][1]), cfg, args)
    return made, ctx, sync.cmd_sync(args, ctx)


def row_of(ctx, name):
    return next(r for r in ctx.captured["repos"] if r["repo"] == name)


# ----------------------------------------------------------------- tests --

def test_status_reports_three_clean_repos_and_touches_nothing():
    with tempfile.TemporaryDirectory() as d:
        made, ctx, rc = run(pathlib.Path(d))
        assert rc == EX_OK
        assert {r["repo"] for r in ctx.captured["repos"]} == {
            "workdir", "porthole", "pmaports"}
        for row in ctx.captured["repos"]:
            assert row["branch"] == "main", row
            assert row["upstream"] == "origin/main", row
            assert (row["ahead"], row["behind"], row["dirty"]) == (0, 0, []), row
        assert ctx.captured["ok"] is True


def test_out_and_in_refuse_without_yes():
    with tempfile.TemporaryDirectory() as d:
        for action in ("out", "in"):
            try:
                run(pathlib.Path(d) / action, action=action)
            except Bail as exc:
                assert exc.code == EX_USAGE, exc.code
            else:
                raise AssertionError(f"sync {action} ran without --yes")


def test_a_dirty_tree_stops_that_repo_and_names_the_file():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        made, cfg = scenario(tmp)
        (made["workdir"][1] / "wip.txt").write_text("half a thought")
        args = _Args("out", yes=True)
        ctx = _Ctx(str(made["porthole"][1]), cfg, args)
        rc = sync.cmd_sync(args, ctx)

        assert rc == EX_FAIL
        row = row_of(ctx, "workdir")
        assert row["dirty"] == ["wip.txt"], row
        assert any("wip.txt" in b for b in row["blocked_by"]), row
        # ...and the file is still there, uncommitted. Nothing was decided.
        assert (made["workdir"][1] / "wip.txt").exists()
        assert "result" not in row, "a dirty repo must not be acted on"


def test_out_pushes_a_committed_change():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        made, cfg = scenario(tmp)
        commit(made["workdir"][1], "new.txt")
        args = _Args("out", yes=True)
        ctx = _Ctx(str(made["porthole"][1]), cfg, args)
        rc = sync.cmd_sync(args, ctx)

        assert rc == EX_OK
        assert row_of(ctx, "workdir")["result"]["action"] == "pushed"
        # The bare remote really has it -- not just what we reported.
        out = git(made["workdir"][0], "log", "--oneline", "main").stdout
        assert "new.txt" in out, out
        assert row_of(ctx, "porthole")["result"]["action"] == "none"


def test_in_fast_forwards_from_the_remote():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        made, cfg = scenario(tmp)
        # A second clone plays the other PC, and pushes.
        other = tmp / "other"
        subprocess.run(("git", "clone", "-q", str(made["workdir"][0]), str(other)),
                       check=True)
        git(other, "config", "user.email", "t@example.invalid")
        git(other, "config", "user.name", "t")
        commit(other, "from-the-other-pc.txt")
        git(other, "push", "-q")

        args = _Args("in", yes=True)
        ctx = _Ctx(str(made["porthole"][1]), cfg, args)
        rc = sync.cmd_sync(args, ctx)

        assert rc == EX_OK
        assert row_of(ctx, "workdir")["result"]["action"] == "pulled"
        assert (made["workdir"][1] / "from-the-other-pc.txt").exists()


def test_in_refuses_a_diverged_repo_and_never_merges():
    """The one that matters: both sides moved. `merge --ff-only` would refuse
    anyway -- this asserts we stop BEFORE asking, and say which repo."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        made, cfg = scenario(tmp)
        other = tmp / "other"
        subprocess.run(("git", "clone", "-q", str(made["workdir"][0]), str(other)),
                       check=True)
        git(other, "config", "user.email", "t@example.invalid")
        git(other, "config", "user.name", "t")
        commit(other, "theirs.txt")
        git(other, "push", "-q")
        commit(made["workdir"][1], "mine.txt")          # local moved too
        git(made["workdir"][1], "fetch", "-q")          # so ahead AND behind

        args = _Args("in", yes=True)
        ctx = _Ctx(str(made["porthole"][1]), cfg, args)
        rc = sync.cmd_sync(args, ctx)

        assert rc == EX_FAIL
        row = row_of(ctx, "workdir")
        assert row["ahead"] and row["behind"], row
        assert any("diverged" in b for b in row["blocked_by"]), row
        assert not (made["workdir"][1] / "theirs.txt").exists(), \
            "a diverged repo was merged anyway"


def test_a_repo_tracking_nothing_is_refused_with_the_command_to_fix_it():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        made, cfg = scenario(tmp)
        git(made["pmaports"][1], "checkout", "-qb", "orphan")

        args = _Args("out", yes=True)
        ctx = _Ctx(str(made["porthole"][1]), cfg, args)
        rc = sync.cmd_sync(args, ctx)

        assert rc == EX_FAIL
        row = row_of(ctx, "pmaports")
        assert row["upstream"] == "", row
        assert any("push -u" in b for b in row["blocked_by"]), row


def test_the_expected_branch_is_reported_and_changes_no_exit_code():
    with tempfile.TemporaryDirectory() as d:
        made, ctx, rc = run(pathlib.Path(d),
                            PORTHOLE_PMAPORTS_BRANCH="taimen-bringup")
        assert rc == EX_OK, "a branch mismatch must not fail the run"
        row = row_of(ctx, "pmaports")
        assert row["branch"] == "main"
        assert row["expected_branch"] == "taimen-bringup"
        assert any("taimen-bringup" in ln for ln in ctx.out.lines), ctx.out.lines


def test_a_missing_repo_is_reported_not_crashed_on():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        made, cfg = scenario(tmp)
        cfg["PORTHOLE_WORKDIR"] = str(tmp / "gone")
        args = _Args(None)
        ctx = _Ctx(str(made["porthole"][1]), cfg, args)
        rc = sync.cmd_sync(args, ctx)

        assert rc == EX_FAIL
        assert row_of(ctx, "workdir")["error"] == "no such directory"
        # The other two were still surveyed; one bad repo does not end the run.
        assert row_of(ctx, "pmaports")["branch"] == "main"


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
