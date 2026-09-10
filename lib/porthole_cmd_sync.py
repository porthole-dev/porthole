#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole sync` -- move between hosts without losing a commit.

Three repos hold this port: the device working repo, porthole itself, and
pmaports. Moving to the other PC means all three, and the failure everyone
has actually had is not a merge conflict -- it is *forgetting one*, then
building for an hour against a tree that is a week behind.

WHAT IT WILL NOT DO
    Never invents a commit, never rewrites history, never force-pushes,
    never resolves a conflict. `in` is `merge --ff-only`, so git itself
    refuses anything that is not a fast-forward; `out` is a plain `push`,
    which git refuses unless it is one. The failure mode is "it stopped and
    told you", and that is the whole design (docs/DESIGN-fork-provenance-
    and-host-sync.md section 9).

    A dirty tree stops the repo it is in, naming the files. Whether that WIP
    is a commit or garbage is not a machine's call.

WHY THE BRANCH IS REPORTED AND NEVER ENFORCED
    Section 9 asked for an assertion. Measured before writing it: on the
    reference host pmaports sits on `perf/crossdirect-native-link` -- not
    `edge`, and not the `taimen-bringup` that porthole_cmd_channel.py's
    comments still name. Sitting on a feature branch IS the normal working
    state, so an assertion would fire on a healthy tree, and a check that
    fires on a healthy tree gets muted. It is printed next to the branch the
    repo is actually on, and the exit code never depends on it.
"""
from __future__ import annotations

import pathlib
import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE, child_env
from porthole_cmd_use import resolve_target

# The three of section 9, in the order a person reads them: their own work
# first, the tool second, the distro tree last.
REPOS = (
    ("workdir", "the device working repo"),
    ("porthole", "porthole itself"),
    ("pmaports", "pmaports"),
)

# Which config key, if any, names the branch a repo is expected to be on.
# Only pmaports has one -- section 9 asks for exactly this key, and inventing
# two more for repos nobody has complained about is how config grows.
EXPECTED_BRANCH_KEY = {"pmaports": "PORTHOLE_PMAPORTS_BRANCH"}


# Reading a ref is local and instant; `push` and `fetch` cross the network.
# Both get a ceiling, because the alternative is what the first version did:
# no timeout at all, so an unreachable remote left `porthole sync` sitting
# with a blank screen and no way to tell waiting from hung. `_git_read` one
# module over has carried a 10s ceiling for exactly this reason since it was
# written; this is that rule applied to the verb that actually goes out.
LOCAL_TIMEOUT = 15
NETWORK_TIMEOUT = 300


def git(repo: pathlib.Path, *args, cfg=None, timeout=LOCAL_TIMEOUT):
    """Run one git command in `repo`. Returns (rc, stdout, stderr), stripped.

    Never raises on a non-zero git: every caller here has something more
    useful to say about a failure than a traceback, and several EXPECT one
    (`@{upstream}` on a branch that has none). A timeout is reported the same
    way, as a non-zero with a message, so no caller has to grow a handler for
    "it did not finish" separate from "it did not work".

    GIT_TERMINAL_PROMPT=0 because stdout and stderr are captured here. In a
    terminal git prompts on /dev/tty and a person can answer; with no tty --
    an agent, a cron, a CI job -- git without this either fails with a
    baffling `No such device or address` or, worse, waits. Refusing to prompt
    turns both into one clear "authentication needed".
    """
    env = child_env(cfg=cfg)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    try:
        p = subprocess.run(("git", "-C", str(repo)) + args,
                           capture_output=True, text=True, env=env,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, "", f"git {args[0]} gave up after {timeout}s"
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def survey(name: str, path: pathlib.Path, cfg) -> dict:
    """Everything `sync` knows about one repo, gathered once.

    One survey feeds the report, the push and the fast-forward, so all three
    judge the same tree. Reading the state twice is how a report says clean
    and the action then finds it dirty.
    """
    row = {"repo": name, "path": str(path), "present": path.is_dir(),
           "branch": "", "dirty": [], "upstream": "", "ahead": 0, "behind": 0,
           "expected_branch": "", "error": ""}
    if not row["present"]:
        row["error"] = "no such directory"
        return row

    rc, _out, _err = git(path, "rev-parse", "--git-dir", cfg=cfg)
    if rc != 0:
        row["error"] = "not a git repository"
        return row

    _rc, row["branch"], _e = git(path, "rev-parse", "--abbrev-ref", "HEAD", cfg=cfg)
    key = EXPECTED_BRANCH_KEY.get(name, "")
    row["expected_branch"] = cfg.get(key, "") if key else ""

    # Porcelain v1, -z: a filename with a space or a newline in it survives,
    # and one with a quote does not get shell-quoted into something that is
    # not the path. Names are what this prints, so they have to be right.
    _rc, out, _e = git(path, "status", "--porcelain", "-z", cfg=cfg)
    row["dirty"] = [f[3:] for f in out.split("\0") if len(f) > 3]

    rc, up, _e = git(path, "rev-parse", "--abbrev-ref", "@{upstream}", cfg=cfg)
    if rc != 0:
        return row                       # no tracking branch; not an error yet
    row["upstream"] = up
    rc, counts, _e = git(path, "rev-list", "--left-right", "--count",
                         f"HEAD...{up}", cfg=cfg)
    if rc == 0 and "\t" in counts:
        ahead, behind = counts.split("\t")[:2]
        row["ahead"], row["behind"] = int(ahead), int(behind)
    return row


def _blockers(row: dict, action: str) -> list[str]:
    """Why this repo cannot be synced right now. Empty means go.

    Shared by both directions because the first three reasons are the same
    ones, and a rule enforced in one direction only is the rule people trip
    over in the other.
    """
    if row["error"]:
        return [row["error"]]
    if row["dirty"]:
        shown = ", ".join(row["dirty"][:5])
        more = f" (+{len(row['dirty']) - 5} more)" if len(row["dirty"]) > 5 else ""
        return [f"uncommitted changes: {shown}{more}"]
    if not row["upstream"]:
        return [f"{row['branch']} tracks nothing; "
                f"`git -C {row['path']} push -u <remote> {row['branch']}` once"]
    if action == "in" and row["ahead"] and row["behind"]:
        return [f"diverged from {row['upstream']}: "
                f"{row['ahead']} ahead, {row['behind']} behind"]
    return []


def _act(row: dict, action: str, cfg) -> dict:
    """Push or fast-forward one repo. Assumes _blockers() already passed."""
    path = pathlib.Path(row["path"])
    # The remote and branch are SPELLED OUT rather than left to bare `git
    # push`. Bare push obeys the host's push.default: `simple` (git's default
    # since 2.0, and what all three repos resolve to here) pushes the current
    # branch and nothing else, but a host configured `matching` pushes every
    # branch whose name exists on the remote. A sync verb must not do
    # something different on someone else's machine.
    remote, _, up_branch = row["upstream"].partition("/")
    if action == "out":
        if not row["ahead"]:
            return {"action": "none", "detail": "already pushed"}
        rc, out, err = git(path, "push", remote,
                           f"{row['branch']}:{up_branch}", cfg=cfg,
                           timeout=NETWORK_TIMEOUT)
        return ({"action": "pushed", "detail": f"{row['ahead']} commit(s) "
                                               f"-> {row['upstream']}"}
                if rc == 0 else {"action": "failed", "detail": err or out})

    rc, out, err = git(path, "fetch", remote, cfg=cfg, timeout=NETWORK_TIMEOUT)
    if rc != 0:
        return {"action": "failed", "detail": err or out}
    # Re-read after the fetch: `behind` was measured against the old ref, and
    # a fetch is precisely the thing that changes it.
    fresh = survey(row["repo"], path, cfg)
    if blocked := _blockers(fresh, "in"):
        return {"action": "blocked", "detail": blocked[0]}
    if not fresh["behind"]:
        return {"action": "none", "detail": "already up to date"}
    rc, out, err = git(path, "merge", "--ff-only", fresh["upstream"], cfg=cfg)
    return ({"action": "pulled",
             "detail": f"{fresh['behind']} commit(s) from {fresh['upstream']}"}
            if rc == 0 else {"action": "failed", "detail": err or out})


def _render(ctx, rows: list[dict], action: str) -> None:
    out = ctx.out
    width = max(len(r["repo"]) for r in rows)
    for row in rows:
        if row["error"]:
            out.kv(row["repo"], row["error"], width, note=row["path"])
            continue
        state = row["branch"]
        if row["expected_branch"] and row["expected_branch"] != row["branch"]:
            state += f"  (profile says {row['expected_branch']})"
        counts = []
        if row["ahead"]:
            counts.append(f"{row['ahead']} to push")
        if row["behind"]:
            counts.append(f"{row['behind']} to pull")
        if row["dirty"]:
            counts.append(f"{len(row['dirty'])} uncommitted")
        if not row["upstream"]:
            counts.append("tracks nothing")
        out.kv(row["repo"], state, width,
               note=", ".join(counts) if counts else "in sync")
        if row.get("result"):
            out(f"      {row['result']['action']}: {row['result']['detail']}")
        for why in row.get("blocked_by", []):
            out(f"      stopped: {why}")
        for name in (row["dirty"] if row.get("blocked_by") else [])[:20]:
            out(f"        {name}")

    # Section 11 question 2 of the design asks whether `sync` covers the
    # kernel tree and answers "no, LOUDLY -- a sync verb that silently skips
    # a tree is worse than one that refuses it". This is the loudly. It is
    # printed in every mode, not only `status`, because the mode where
    # somebody assumes their kernel went with the rest is `out`.
    out.kv("kernel", "not synced, by design", width,
           note="not mirrored between hosts; see docs/NEW-HOST.md")

    if action == "status":
        out.blank()
        out.hint("porthole sync out --yes", "push what is committed in each")
        out.hint("porthole sync in --yes", "fast-forward each")


def cmd_sync(args, ctx) -> int:
    action = args.action or "status"
    if action in ("out", "in") and not getattr(args, "yes", False):
        raise Bail(f"`sync {action}` writes to repos outside this one",
                   EX_USAGE, f"porthole sync {action} --yes")

    rows = []
    for name, _what in REPOS:
        try:
            path = resolve_target(name, ctx.cfg, ctx.root)
        except Bail as exc:
            rows.append({"repo": name, "path": "", "present": False,
                         "branch": "", "dirty": [], "upstream": "",
                         "ahead": 0, "behind": 0, "expected_branch": "",
                         "error": str(exc)})
            continue
        rows.append(survey(name, path, ctx.cfg))

    bad = False
    for row in rows:
        if action == "status":
            bad = bad or bool(row["error"])
            continue
        if blocked := _blockers(row, action):
            row["blocked_by"], bad = blocked, True
            continue
        row["result"] = _act(row, action, ctx.cfg)
        bad = bad or row["result"]["action"] in ("failed", "blocked")

    ctx.emit({"action": action, "repos": rows, "ok": not bad,
              "kernel_tree": "not synced -- not mirrored between hosts"},
             lambda: _render(ctx, rows, action))
    return EX_FAIL if bad else EX_OK


SPEC = {
    "verb": "sync",
    "order": 41,
    "group": "sources",
    "help": "move the three repos between hosts: report, push, or fast-forward",
    "description": (
        "The device working repo, porthole, and pmaports -- the three that\n"
        "have to agree for a build on the other PC to mean anything.\n\n"
        "With no action it REPORTS: what branch each is on, what is\n"
        "uncommitted, and how far each is from its tracking branch. That is\n"
        "read-only and the one worth running before you start.\n\n"
        "`out` pushes what is committed in each. `in` fetches and\n"
        "fast-forwards each. Both STOP on a repo with uncommitted changes\n"
        "and name the files, and `in` also stops on a repo that has\n"
        "diverged. Nothing here invents a commit, rewrites history, resolves\n"
        "a conflict or force-pushes -- `in` is `merge --ff-only` and `out`\n"
        "is a plain `push`, so git itself refuses anything else.\n\n"
        "PORTHOLE_PMAPORTS_BRANCH, if set, is REPORTED beside the branch\n"
        "pmaports is actually on. It never changes the exit code: a feature\n"
        "branch there is the normal working state, and a check that fires on\n"
        "a healthy tree is one people learn to ignore.\n\n"
        "See docs/DESIGN-fork-provenance-and-host-sync.md section 9."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["status", "out", "in"],
                      "help": "status | out | in   (default status)"}),
        (["--yes"], {"action": "store_true",
                     "help": "out/in: actually write to the other repos"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    # `in` and `out` write to repos outside PORTHOLE_ROOT, which is what this
    # flag means and why --yes is required above.
    "escapes_scope": True,
    "run": cmd_sync,
    "examples": [
        "porthole sync",
        "porthole sync --json",
        "porthole sync out --yes",
        "porthole sync in --yes",
    ],
}
