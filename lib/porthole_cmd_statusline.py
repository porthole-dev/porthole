#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole statusline` -- a live build bar in the agent's own UI.

WHY THIS EXISTS
    porthole already publishes everything a progress display needs: a build
    writes `.run/build-status.json` the whole time it runs, and `porthole
    build watch` paints a bar from it. But `watch` paints with carriage
    returns, and an agent that runs it inside a tool call sends that repaint
    stream into a captured pipe. The human sees either nothing until the build
    ends, or the smeared, column-overlapped mess that prompted this file. A
    redrawing bar cannot travel through an agent's tool output; it has to be
    drawn by something the human's terminal owns.

    Claude Code's status line is exactly that: it runs a command of your
    choosing, on a timer, and paints the result at the bottom of the screen.
    Not tool output, so it costs no tokens, survives the agent being busy, and
    keeps repainting while the model sits idle waiting for a build.
    docs: https://code.claude.com/docs/en/statusline

    So an agent's job stops being "show the human progress" -- which it cannot
    do -- and becomes "start the build detached and get out of the way":

        porthole build fast --yes --detach

WHY A VERB RATHER THAN A SCRIPT PATH
    The settings file has to name a command, and every project that wants the
    bar needs the same one. A path -- `~/src/porthole/.claude/
    statusline.py` -- is a config that works on exactly one desk, which is the
    no-hardcoded-values rule. `porthole statusline` has no path in it, so the
    same six lines of JSON are correct in every project and on every machine.

WHAT IT PRINTS
    WHATEVER YOU ALREADY HAD, then a build row when there is a build.

    This APPENDS; it does not replace, and the first version did. A settings
    file holds ONE statusLine, and a project one shadows the user one -- so
    installing this into a project silently took a three-line status display
    (rate limits with reset times, full path and branch, the skills used this
    session) and substituted a worse copy of its first line. Reported the same
    day it landed, and it deserved to be.

    So the inherited command is discovered at render time from ~/.claude and
    run with the same stdin, and its output is passed through verbatim -- ANSI
    and all, however many lines it is. Discovered rather than copied into the
    project settings, because the copy would go stale the moment the person
    edits their own status line, and they would never think to look here.

    Only when nothing is inherited does this print a line of its own, so a
    fresh install still says something.

    The build row is appended only when a build is worth showing -- running,
    or finished within the last few minutes so its result is still on screen.
    No build, no row; the status line must not grow a permanently empty one.

    The bar is `porthole_progress.line_of`, the SAME renderer `porthole build
    watch` uses. Two implementations of one bar is how a watcher ends up
    disagreeing with the thing it is watching.

NEVER RAISES WHILE RENDERING
    A status line that throws prints its traceback into the user's chrome, or
    nothing at all, every 2 seconds. Every read on the render path is
    best-effort and the fallback is always "print less", never "print an
    error". `--install` is the opposite: it is a one-shot the human is
    watching, so it fails loudly.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

from porthole_cli import Bail, EX_FAIL, EX_OK

# Show a finished run for this long, so the human sees how it ended rather
# than the line vanishing at the instant the result becomes interesting.
LINGER_S = 300.0

# `refreshInterval` is the load-bearing field, not a nicety. Without it the
# status line re-runs only on session events -- a new assistant message, a
# permission-mode change -- and a detached build advancing in the background
# is precisely the case where no session events happen. The bar would freeze
# at whatever it said when the agent last spoke.
SETTINGS = {
    "statusLine": {
        "type": "command",
        "command": "porthole statusline",
        "refreshInterval": 2,
    }
}

# settings.local.json, never settings.json, for a project porthole does not
# own. A kernel tree is somebody's git repo and a bar in an agent's chrome is
# a personal preference, not a fact about the source. Claude Code treats
# `.local` as exactly that.
LOCAL_SETTINGS = pathlib.Path(".claude") / "settings.local.json"

# The status line has to be quick, and the inherited command is somebody
# else's script -- this developer's reads the whole session transcript with
# jq. Bounded so a slow one degrades to "no inherited line" rather than to a
# status line that never repaints.
BASE_TIMEOUT_S = 5.0


def _base_command() -> str:
    """The status line this one is standing in front of, or "".

    Read from the USER's settings, never the project's: the project file is
    the one holding this verb, and reading it would find ourselves.
    """
    override = os.environ.get("PORTHOLE_STATUSLINE_BASE")
    if override is not None:
        return override.strip()
    home = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR")
                        or pathlib.Path.home() / ".claude")
    # Local wins over settings.json, matching the harness's own precedence.
    for name in ("settings.local.json", "settings.json"):
        try:
            data = json.loads((home / name).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        cmd = ((data.get("statusLine") or {}).get("command") or "").strip()
        # A user-level `porthole statusline` would call itself forever.
        if cmd and "porthole statusline" not in cmd:
            return cmd
    return ""


def _run_base(command: str, stdin_text: str) -> str:
    """Its stdout, verbatim, or "" if it failed. Never raises."""
    try:
        proc = subprocess.run(["bash", "-c", command], input=stdin_text,
                              capture_output=True, text=True,
                              timeout=BASE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout


# ------------------------------------------------------------ rendering ----

def _repo(inp) -> pathlib.Path:
    """The porthole checkout.

    The harness's own idea of the project first, so a session opened inside
    porthole resolves to itself; otherwise this file's own location, which is
    what makes the verb work from a device workdir that is nowhere near it.
    """
    for key in ("project_dir", "current_dir"):
        val = (inp.get("workspace") or {}).get(key)
        if val:
            here = pathlib.Path(val)
            for cand in (here, *here.parents):
                if (cand / "lib" / "porthole_progress.py").is_file():
                    return cand
    return pathlib.Path(__file__).resolve().parent.parent


def _snap(rundir: pathlib.Path, name: str):
    try:
        return json.loads((rundir / name).read_text())
    except (OSError, ValueError):
        return None


def build_line(repo: pathlib.Path, width: int, now=None):
    """The build row, or None when there is nothing worth a row.

    Both status files are considered and the more recent one wins: a kernel
    rung and a package build do not run at once, but the file from the last
    one that did sticks around, and picking by name would show the older.
    """
    import porthole_progress as pp
    now = time.time() if now is None else now
    rundir = repo / ".run"
    best = None
    for name in ("build-status.json", "pkg-status.json"):
        snap = _snap(rundir, name)
        if not snap:
            continue
        if best is None or (snap.get("last_at") or 0) > (best.get("last_at") or 0):
            best = snap
    if not best:
        return None
    state = pp.liveness(best)
    if state != "running" and now - (best.get("last_at") or 0) > LINGER_S:
        return None      # old news; the row would be clutter, not status
    # No state tag here: line_of renders the finished states itself now, and
    # `fast done ... done` reads as a rendering bug.
    body = pp.line_of(best, budget=max(40, width - 10))
    return pp.clip("{:<8}{}".format(best.get("rung") or "build", body), width)


def session_line(inp, repo: pathlib.Path) -> str:
    model = (inp.get("model") or {}).get("display_name") or "claude"
    cwd = (inp.get("workspace") or {}).get("current_dir") or str(repo)
    pct = (inp.get("context_window") or {}).get("used_percentage")
    branch = ""
    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=1).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    bits = [model, pathlib.Path(cwd).name]
    if branch:
        bits.append(branch)
    if isinstance(pct, (int, float)):
        bits.append("{:.0f}% ctx".format(pct))
    return " · ".join(bits)


def _render() -> int:
    raw, inp = "", {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            inp = json.loads(raw or "{}")
        except (ValueError, OSError):
            inp = {}
    if not isinstance(inp, dict):
        inp = {}
    try:
        width = max(40, int(os.environ.get("COLUMNS") or 100) - 2)
    except ValueError:
        width = 98
    repo = _repo(inp)
    # Their status line first, unchanged. Only if there is none -- or it fails
    # -- does this substitute one, so the fallback can never quietly replace a
    # display somebody built.
    inherited = _base_command()
    base = _run_base(inherited, raw) if inherited else ""
    if base.strip():
        sys.stdout.write(base if base.endswith("\n") else base + "\n")
    else:
        print(session_line(inp, repo))
    try:
        row = build_line(repo, width)
    except Exception:      # noqa: BLE001 -- see NEVER RAISES above
        row = None
    if row:
        print(row)
    return EX_OK


# -------------------------------------------------------------- install ----

def _git_excluded(project: pathlib.Path, rel: str) -> bool:
    try:
        return subprocess.run(["git", "-C", str(project), "check-ignore", "-q", rel],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True      # not a git repo, or no git: nothing to leak into


def _exclude_locally(project: pathlib.Path, rel: str) -> str:
    """.git/info/exclude, NOT .gitignore.

    The file is a personal preference inside somebody else's repo -- often a
    kernel tree -- so ignoring it must not show up as a tracked change. That
    is exactly what info/exclude is for, and it is the difference between
    installing this and dirtying a tree the porter is mid-rebase on.
    """
    try:
        top = subprocess.run(["git", "-C", str(project), "rev-parse",
                              "--git-dir"], capture_output=True, text=True,
                             timeout=5)
        if top.returncode != 0:
            return ""
        gitdir = (project / top.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        return ""
    path = gitdir / "info" / "exclude"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = path.read_text() if path.exists() else ""
        if rel in body.split():
            return str(path)
        with path.open("a") as fh:
            if body and not body.endswith("\n"):
                fh.write("\n")
            fh.write(f"{rel}\n")
        return str(path)
    except OSError:
        return ""


def _install(ctx, args) -> int:
    project = pathlib.Path(getattr(args, "project", None)
                           or os.getcwd()).expanduser().resolve()
    if not project.is_dir():
        raise Bail(f"{project} is not a directory", EX_FAIL,
                   "name the project to install into with --project")
    target = project / LOCAL_SETTINGS
    # Merge rather than overwrite: settings.local.json is where a person keeps
    # their own permissions and model choice, and a statusline install that
    # silently ate those would be a bug worth more than this feature.
    existing = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text())
        except ValueError as exc:
            raise Bail(f"{target} is not valid JSON", EX_FAIL,
                       f"fix or move it first ({exc})") from None
        if not isinstance(existing, dict):
            raise Bail(f"{target} is not a JSON object", EX_FAIL,
                       "fix or move it first")
    had = existing.get("statusLine")
    merged = dict(existing)
    merged.update(SETTINGS)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2) + "\n")

    rel = LOCAL_SETTINGS.as_posix()
    excluded = _git_excluded(project, rel)
    exclude_file = "" if excluded else _exclude_locally(project, rel)

    base = _base_command()

    def render():
        o = ctx.out
        o(f"  wrote {target}")
        if base:
            o(o.paint(f"  chaining your existing status line: {base}", "grey"))
            o(o.paint("  it is run first and passed through unchanged; the "
                      "build row is appended", "grey"))
        if had and had != SETTINGS["statusLine"]:
            o(o.paint(f"  replaced an existing statusLine: {had}", "yellow"))
        if exclude_file:
            o(o.paint(f"  ignored via {exclude_file} -- not a tracked change",
                      "grey"))
        elif excluded:
            o(o.paint("  already ignored by this repo", "grey"))
        o.blank()
        o.hint("start a build detached and the bar draws itself: "
               "porthole build fast --yes --detach")

    return ctx.emit({"path": str(target), "replaced": had, "chains": base,
                     "excluded": bool(excluded or exclude_file)}, render)


def cmd_statusline(args, ctx) -> int:
    if getattr(args, "install", False):
        return _install(ctx, args)
    return _render()


SPEC = {
    "verb": "statusline",
    "order": 17,
    "help": "render the build bar for an agent's status line, or install it",
    "description": (
        "A live build bar in the agent's own UI, so nobody has to watch a\n"
        "tool call. `porthole build watch` repaints with carriage returns,\n"
        "and an agent running it inside a tool call sends that into a pipe --\n"
        "the human gets a smeared mess or nothing at all. A repainting bar\n"
        "has to be drawn by something the human's terminal owns.\n\n"
        "With no flags this reads Claude Code's session JSON on stdin and\n"
        "prints the line. `--install` wires it into a project's\n"
        ".claude/settings.local.json, ignored via .git/info/exclude so it is\n"
        "never a tracked change in somebody's kernel tree.\n\n"
        "Only Claude Code can consume this today; OpenCode and Codex have it\n"
        "as open feature requests. The portable half is already there for\n"
        "them: .run/build-status.json and `porthole build watch --json`."),
    # stdout is a rendered status line, not a report.
    "reports": False,
    "args": [
        (["--install"], {"action": "store_true",
                         "help": "wire it into a project's .claude settings"}),
        (["--project"], {"metavar": "DIR",
                         "help": "install into DIR (default: the cwd)"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_statusline,
    "examples": [
        "porthole statusline --install",
        "porthole statusline --install --project ~/src/taimen",
    ],
}
