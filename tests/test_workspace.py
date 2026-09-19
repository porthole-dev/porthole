#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole workspace`: the inventory, and the distinction it exists for.

Real git repositories throughout, never fakes: every field here comes from
shelling out to git, and a fake would only prove the fake works. The one that
matters most -- worktree versus clone -- cannot be faked meaningfully at all,
because the whole point is that the two are hard to tell apart.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "lib"))

import _runner                                              # noqa: E402
import porthole_cmd_workspace as ws                         # noqa: E402

ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.x",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.x",
       "PATH": os.environ.get("PATH", "")}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=ENV).stdout.strip()


def repo(path, branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "Makefile").write_text("# kernel\n")
    git(path, "init", "-q", "-b", branch)
    git(path, "add", "Makefile")
    git(path, "commit", "-qm", "base")
    return path


def test_a_worktree_is_told_apart_from_a_clone():
    """The distinction this module exists for, and the one an assistant got
    wrong here with the measurement in front of it.

    A linked worktree shares its parent's object store, so `git remote
    get-url` answers the SAME for both -- which reads as two clones of one
    remote and is not. The assertion on `origin` is deliberate: it pins the
    fact that the field everyone reaches for cannot decide this, so that
    deleting `linked` as redundant fails here rather than in a conclusion.
    """
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d)
        parent = repo(base / "linux")
        git(parent, "remote", "add", "origin", "https://example.invalid/linux.git")
        git(parent, "worktree", "add", "-q", "-b", "camera",
            str(base / "linux-ws"))

        clone = ws.survey(parent)
        linked = ws.survey(base / "linux-ws")
        assert clone["linked"] is False, clone
        assert linked["linked"] is True, linked
        assert clone["origin"] == linked["origin"] != "", (
            "if the remotes ever differ this test has stopped covering the "
            "case that caused the mistake", clone["origin"], linked["origin"])


def test_a_checkout_nested_in_the_working_repo_is_found():
    """The bug the first version shipped with. The working repo is ITSELF a
    git repository with the kernel trees inside it, so a walk that stopped
    descending at the first checkout reported the workdir, missed every tree
    in it, and said "1 of 1" while looking healthy."""
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d) / "work"
        repo(work)
        repo(work / "linux", branch="taimen-v7.2")
        repo(work / "ref" / "downstream", branch="vendor")
        found = {p.name for p in ws.find_checkouts(work)}
        assert found == {"work", "linux", "downstream"}, found


def test_the_walk_is_bounded_and_skips_noise():
    """An unbounded walk over a kernel tree is 80k directories. The depth
    bound is what makes this a command you run rather than one you avoid."""
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d) / "work"
        work.mkdir(parents=True)
        deep = work / "a" / "b" / "c" / "d"
        repo(deep)
        repo(work / "node_modules" / "pkg")
        found = {p.name for p in ws.find_checkouts(work)}
        assert "d" not in found, ("past MAX_DEPTH", found)
        assert "pkg" not in found, ("node_modules must not be walked", found)


def test_the_registered_tree_is_not_a_stray():
    """The positive control for "N strays": with nothing ever registered the
    count is unfalsifiable and the warning is noise on a correct setup."""
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d) / "work"
        tree = repo(work / "linux")
        known = ws.registered(
            {"PORTHOLE_WORKDIR": str(work),
             "PORTHOLE_KERNEL_TREE": str(tree)}, None)
        assert tree.resolve() in known, known
        assert known[tree.resolve()] == "PORTHOLE_KERNEL_TREE", known
        assert (work / "nothing").resolve() not in known


def test_shallow_is_reported():
    """Shallow is the cause the report leads with, so it has to be read off
    the repository rather than assumed."""
    with tempfile.TemporaryDirectory() as d:
        tree = repo(pathlib.Path(d) / "linux")
        assert ws.survey(tree)["shallow"] is False
        (tree / ".git" / "shallow").write_text("")
        assert ws.survey(tree)["shallow"] is True


def test_a_missing_git_never_raises():
    """The inventory must degrade to blanks, not explode: it is run on a desk
    whose whole problem is that something is in a state nobody expected."""
    with tempfile.TemporaryDirectory() as d:
        row = ws.survey(pathlib.Path(d))          # a plain directory
        assert row["branch"] == "detached", row
        assert row["origin"] == "" and row["shallow"] is False, row


def test_the_verb_is_read_only():
    """No action and no writing flag. A `prune` action was written and cut
    before shipping -- it wrapped `git worktree prune` and did nothing git
    does not already do."""
    flags = [names[0] for names, _kw in ws.SPEC["args"]]
    assert flags == ["--json"], flags
    assert "READ-ONLY" in ws.SPEC["description"], ws.SPEC["description"]



def test_one_unshallow_line_per_repository_not_per_checkout():
    """A worktree and its parent share an object store, so `--unshallow` on
    either deepens both. Printing it twice invites running it twice and reads
    as two problems where there is one."""
    rows = [
        {"path": "/w/linux", "common_dir": "/w/linux/.git"},
        {"path": "/w/linux-ws", "common_dir": "/w/linux/.git",
         "via_worktree": True},
    ]
    assert ws.unshallow_targets(rows) == ["/w/linux"], \
        ws.unshallow_targets(rows)


def test_a_worktree_cannot_be_deepened_on_its_own():
    """Its objects are the parent's, so the command has to name the parent
    even when the worktree is the only shallow row."""
    rows = [{"path": "/w/linux-ws", "common_dir": "/w/linux/.git",
             "via_worktree": True}]
    assert ws.unshallow_targets(rows) == ["/w/linux"], \
        ws.unshallow_targets(rows)


def test_a_worktree_of_a_registered_tree_is_not_a_stray():
    """It is the cheap, correct way to have two branches of one history --
    the thing you WANT people doing. Calling it sprawl pushes them back
    toward the second clone that causes the real thing.

    This is checked through the verb because the attribution happens there,
    over the whole row set, and it is the row set that makes it decidable.
    """
    import types
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d) / "work"
        work.mkdir(parents=True)
        tree = repo(work / "linux")
        git(tree, "worktree", "add", "-q", "-b", "camera",
            str(work / "linux-ws"))

        lines, payloads = [], []

        class Out:
            def __call__(self, text=""):
                lines.append(text)

            def heading(self, text):
                lines.append(text)

            def blank(self):
                lines.append("")

            def paint(self, text, _colour=None):
                return text

        class Ctx:
            out = Out()
            root = None
            cfg = {"PORTHOLE_WORKDIR": str(work),
                   "PORTHOLE_KERNEL_TREE": str(tree)}

            def emit(self, payload, render=None):
                payloads.append(payload)
                if render:
                    render()
                return 0

        ws.cmd_workspace(types.SimpleNamespace(json=False), Ctx())
        payload = payloads[0]
        assert str(work / "linux-ws") not in payload["strays"], payload["strays"]
        row = next(r for r in payload["checkouts"]
                   if r["path"] == str(work / "linux-ws"))
        assert row["registered"].startswith("worktree of"), row


def test_a_deliberately_shallow_reference_clone_is_not_nagged_about():
    """ref/ clones are shallow on purpose -- you read them, you never send a
    series from one. Advising `--unshallow` there is advice on a healthy
    tree, and a check that fires on one gets muted."""
    import types
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d) / "work"
        work.mkdir(parents=True)
        ref = repo(work / "ref" / "downstream-vendor")
        (ref / ".git" / "shallow").write_text("")

        payloads = []

        class Out:
            def __call__(self, text=""):
                pass

            def heading(self, text):
                pass

            def blank(self):
                pass

            def paint(self, text, _colour=None):
                return text

        class Ctx:
            out = Out()
            root = None
            cfg = {"PORTHOLE_WORKDIR": str(work)}

            def emit(self, payload, render=None):
                payloads.append(payload)
                if render:
                    render()
                return 0

        ws.cmd_workspace(types.SimpleNamespace(json=False), Ctx())
        payload = payloads[0]
        assert payload["shallow"] == [], (
            "an unregistered reference clone must not be told to unshallow",
            payload["shallow"])
        # ...but it is not hidden either: it is still reported, just not nagged.
        assert str(ref) in payload["shallow_unregistered"], payload


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
