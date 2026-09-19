#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole workspace` -- every checkout on this desk, and which is which.

THE QUESTION IT ANSWERS, AND WHY NOTHING ELSE DID
    "Which of these `linux*` directories is which, and which one will my build
    actually use." `porthole sync` covers three named repos and stops;
    `porthole doctor` has one cheap row for the selected kernel tree. Neither
    shows the desk, so the answer lived in a manual `find` and in whoever
    remembered.

    The field that earns this module is `linked`. A linked WORKTREE and a
    clone look identical from the outside, and `git remote get-url` answers
    the same for both -- a worktree shares its parent's object store, so it
    reports the parent's remote BECAUSE they share it. Read that as evidence
    of two clones and you conclude a correct setup is 20 GB of duplicated
    history. That mistake was made in this repository, by an assistant, with
    the measurement in front of it. The tell is the ONE thing that differs: a
    worktree's `.git` is a FILE holding `gitdir:`, a clone's is a directory.

READ-ONLY, ENTIRELY
    No action, no flags that write. It never deletes a checkout, never moves
    one, never fetches, never touches a working tree.

    A `prune` action was written and cut before shipping: it wrapped
    `git worktree prune` in a preview and a --yes and did nothing git does not
    already do on its own. When the fix is one command you already have, the
    verb's job is to NAME it, which is what the report does.

    What this cannot do is keep the desk clean. It tells you what is there.
    The mess is prevented upstream of it -- by a build that refuses a tree no
    config key names, and by trees deep enough that moving a branch onto a new
    base does not require cloning again.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

from porthole_cli import Bail, EX_FAIL

# Three levels covers `<workdir>/<tree>` and `<workdir>/ref/<tree>`, which is
# the shape that occurs, and bounds the walk on a tree with 80k directories.
MAX_DEPTH = 3
SKIP_DIRS = ("node_modules", "__pycache__", "site", "site-src")


def _git(path, *args, timeout=10) -> str:
    """One local git read, or "". Never raises, never hangs.

    Ten seconds for the reason `porthole sync` carries a ceiling: a wedged git
    must not be able to take the command down with it.
    """
    try:
        proc = subprocess.run(["git", "-C", str(path), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def registered(cfg, root) -> dict:
    """{resolved path: which config key named it}. What is NOT a stray."""
    out = {}
    try:
        from porthole_cmd_build import _tree
        tree = _tree(cfg)
        if str(tree) not in ("", "."):
            out[tree.resolve()] = (
                "PORTHOLE_KERNEL_TREE"
                if (cfg.get("PORTHOLE_KERNEL_TREE") or "").strip()
                else "default: <workdir>/linux")
    except Exception:  # noqa: BLE001 -- an inventory must not fail on resolution
        pass
    for key in ("PORTHOLE_WORKDIR", "PORTHOLE_PMAPORTS"):
        value = (cfg.get(key) or "").strip()
        if value:
            try:
                out.setdefault(pathlib.Path(value).expanduser().resolve(), key)
            except OSError:
                pass
    if root:
        out.setdefault(pathlib.Path(root).resolve(), "porthole itself")
    return out


def _common_dir(path: pathlib.Path) -> str:
    """The repository the checkout's objects belong to, absolute, or ""."""
    raw = _git(path, "rev-parse", "--git-common-dir")
    if not raw:
        return ""
    try:
        return str((path / raw).resolve() if not os.path.isabs(raw)
                   else pathlib.Path(raw).resolve())
    except OSError:
        return ""


def survey(path: pathlib.Path) -> dict:
    """Everything worth knowing about one checkout, read once."""
    return {
        "path": str(path),
        "branch": _git(path, "rev-parse", "--abbrev-ref", "HEAD") or "detached",
        # The load-bearing field. See the module docstring.
        "linked": (path / ".git").is_file(),
        "shallow": _git(path, "rev-parse", "--is-shallow-repository") == "true",
        # Where the object store lives. For a clone this is its own `.git`;
        # for a worktree it is the PARENT's, which is what lets a worktree of
        # a registered tree be attributed to it rather than called a stray.
        "common_dir": _common_dir(path),
        "dirty": len([ln for ln in _git(path, "status", "--porcelain").splitlines()
                      if ln.strip()]),
        "origin": _git(path, "remote", "get-url", "origin"),
    }


def find_checkouts(root: pathlib.Path):
    """Every git checkout under `root`, bounded.

    It does NOT stop descending at the first one, and that is the bug this
    shipped with once: the working repo is ITSELF a git repository with the
    kernel trees inside it, so a walk that stopped there reported the workdir,
    missed every tree in it, and said "1 of 1" while looking healthy.
    """
    for current, dirs, _files in os.walk(root, followlinks=False):
        here = pathlib.Path(current)
        if (here / ".git").exists():
            yield here
        dirs[:] = [d for d in dirs
                   if not d.startswith(".") and d not in SKIP_DIRS]
        if len(here.relative_to(root).parts) >= MAX_DEPTH:
            dirs[:] = []


def unshallow_targets(shallow) -> list:
    """One `fetch --unshallow` target per repository, in a stable order.

    A worktree cannot be deepened on its own: the objects live in the parent,
    so the fetch belongs to the repository that owns them rather than to the
    checkout you happen to be standing in. Pure, so the de-duplication is
    testable without a git repository.
    """
    out = []
    for row in shallow:
        where = row["path"]
        if row.get("via_worktree") and row.get("common_dir"):
            where = str(pathlib.Path(row["common_dir"]).parent)
        if where not in out:
            out.append(where)
    return out


def cmd_workspace(args, ctx) -> int:
    workdir = (ctx.cfg.get("PORTHOLE_WORKDIR") or "").strip()
    if not workdir or not pathlib.Path(workdir).is_dir():
        raise Bail("no working repo to inventory", EX_FAIL,
                   "porthole init    finds or creates one, and writes the key")
    root = pathlib.Path(workdir)
    known = registered(ctx.cfg, ctx.root)

    rows = []
    for path in sorted(find_checkouts(root)):
        row = survey(path)
        row["registered"] = known.get(path.resolve(), "")
        rows.append(row)

    # A worktree of a registered tree is NOT a stray. It is the cheap, correct
    # way to have two branches of one history -- exactly what you want people
    # doing -- and calling it sprawl pushes them back toward the second clone
    # that causes the real thing. Attributed by object store, because that is
    # what actually ties it to its parent.
    owned = {r["common_dir"] for r in rows if r["registered"] and r["common_dir"]}
    for row in rows:
        if not row["registered"] and row["linked"] and row["common_dir"] in owned:
            parent = next((r for r in rows
                           if r["registered"]
                           and r["common_dir"] == row["common_dir"]), None)
            row["registered"] = "worktree of {}".format(
                pathlib.Path(parent["path"]).name if parent else "a registered tree")
            row["via_worktree"] = True

    strays = [r for r in rows if not r["registered"]]
    # Shallow is only worth raising on a tree you BUILD or PATCH from. A
    # reference clone under ref/ is deliberately shallow and disposable -- you
    # read it, you never send a series from it -- so advising `--unshallow`
    # there is advice on a healthy tree, and a check that fires on one gets
    # muted (brain/laws/a-check-that-fires-on-a-healthy-tree-gets-muted.md).
    shallow = [r for r in rows if r["shallow"] and r["registered"]]
    payload = {"workdir": str(root), "checkouts": rows,
               "strays": [r["path"] for r in strays],
               "shallow": [r["path"] for r in shallow],
               "shallow_unregistered": [r["path"] for r in rows
                                        if r["shallow"] and not r["registered"]],
               "read_only": True}

    def render():
        ctx.out.heading(f"workspace — {root}")
        ctx.out.blank()
        for row in rows:
            marks = [row["branch"]]
            if row["linked"]:
                marks.append("worktree")
            if row["shallow"]:
                marks.append(ctx.out.paint("SHALLOW", "yellow"))
            if row["dirty"]:
                marks.append(f"{row['dirty']} dirty")
            name = str(pathlib.Path(row["path"]).relative_to(root)) or "."
            tag = (ctx.out.paint("·", "green") if row["registered"]
                   else ctx.out.paint("?", "yellow"))
            ctx.out(f"  {tag} {name:<34} {', '.join(marks)}")
            if row["registered"]:
                ctx.out(f"      via {row['registered']}")
        ctx.out.blank()
        ctx.out(f"  {len(rows)} checkout(s), "
                f"{len(strays)} named by no config key")
        # Shallow first, because it is the CAUSE rather than a symptom: it is
        # what makes a second clone the only way to move a branch onto a new
        # base, so the strays below are downstream of it.
        if shallow:
            ctx.out.blank()
            ctx.out("  SHALLOW — cannot format-patch a series or rebase onto")
            ctx.out("  another base, which is what makes cloning again the "
                    "only way forward:")
            # One line per REPOSITORY, not per checkout. A worktree and its
            # parent share an object store, so `--unshallow` on either
            # deepens both -- printing it twice invites running it twice and
            # reads as two problems where there is one.
            for where in unshallow_targets(shallow):
                ctx.out(f"      git -C {where} fetch --unshallow")
        if strays:
            ctx.out.blank()
            ctx.out("  Nothing here is deleted by this command, and a stray is")
            ctx.out("  often deliberate — a reference tree, someone else's")
            ctx.out("  experiment. Check a dirty one holds nothing you want")
            ctx.out("  before retiring it by hand.")

    return ctx.emit(payload, render)


SPEC = {
    "verb": "workspace",
    "order": 42,
    "group": "sources",
    "help": "every checkout on this desk: registered, worktree, shallow, dirty",
    "description": (
        "One place to see what is actually on this desk. For each git\n"
        "checkout under the working repo: its branch, whether it is a linked\n"
        "WORKTREE or a clone, whether it is SHALLOW, how much is uncommitted,\n"
        "and which config key names it -- or none, which makes it a stray.\n\n"
        "READ-ONLY. It never deletes a checkout, never moves one, never\n"
        "fetches and never touches a working tree. Where there is something\n"
        "to do it prints the command and leaves it to you.\n\n"
        "SHALLOW is the one worth acting on, and the reason the rest\n"
        "accumulates: a shallow tree cannot format-patch a series or rebase\n"
        "onto another base, so when a branch needs a different base a fresh\n"
        "clone becomes the only move -- which is how a desk ends up with\n"
        "trees nobody chose to make.\n\n"
        "A worktree is NOT sprawl. It is the cheap, correct way to have two\n"
        "branches of one history, and it shares its parent's object store --\n"
        "which is why `git remote get-url` answers the same for both and why\n"
        "two of them read as duplicated history when they are not."),
    "args": [
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_workspace,
    "examples": [
        "porthole workspace",
        "porthole workspace --json",
    ],
}
