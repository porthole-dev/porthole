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


# Where pmbootstrap writes its log, host side, for each of the two work dirs
# a build can use. Read from the environment only -- the status line runs
# every two seconds and must not parse config, shell out, or ask podman
# anything. A `stat` is free; `podman exec` is not.
PMB_LOGS = (("PORTHOLE_SANDBOX_PMB_DIR", "~/.local/var/porthole-sandbox"),
            ("PORTHOLE_PMB_DIR", "~/.local/var/pmbootstrap"))

# The freshness window and the skew allowance are `porthole_progress`'s, so
# this file and `pkg status` cannot disagree about whether a log is live.
def _fresh_bounds():
    import porthole_progress as pp

    return pp.LOG_FRESH_S, pp.CLOCK_SKEW_S


def _live_log(now: float):
    """`(path, mtime)` of a pmbootstrap log written just now, else None."""
    best = None
    for env, default in PMB_LOGS:
        path = pathlib.Path(os.environ.get(env) or default).expanduser()
        try:
            mtime = (path / "log.txt").stat().st_mtime
        except OSError:
            continue
        fresh, skew = _fresh_bounds()
        # Bounded at BOTH ends. A log dated in the future -- clock skew, a
        # copied tree, a restored backup -- is not evidence that something is
        # building now, and an unbounded `age <= fresh` accepts every one of
        # them. Checked here as well as in `reattach_from_log`, because this
        # is also what PICKS between the two work dirs' logs.
        if -skew <= now - mtime <= fresh and (best is None
                                              or mtime > best[1]):
            best = (path / "log.txt", mtime)
    return best


# Where the reattached row keeps its rate samples. Each status line run is a
# fresh process, so there is nothing in memory to measure a rate against and
# the ETA -- the one number somebody watching a four-hour build actually
# wants -- was permanently `--`. A handful of `(when, done)` pairs on disk is
# enough, and it is the same shape `porthole_progress.window_rate` consumes.
SAMPLES_NAME = "statusline-samples.json"

# 180s of window at a 2s refresh is 90 samples; generous enough to survive a
# slower refresh, small enough that the file stays a few hundred bytes.
SAMPLES_MAX = 128


def _samples(rundir: pathlib.Path, mtime: float, done, now: float):
    """The rolling `(when, done)` ring, updated with this reading.

    Never raises: an unwritable .run costs the ETA, not the status line.
    """
    import porthole_progress as pp
    path = rundir / SAMPLES_NAME
    try:
        kept = [(float(a), int(b))
                for a, b in json.loads(path.read_text())][-SAMPLES_MAX:]
    except (OSError, ValueError, TypeError):
        kept = []
    # A counter that went backwards is a NEW build. `window_rate` would refuse
    # the negative rate, but the stale pairs would sit in the window for
    # minutes afterwards, so the ETA would stay `--` long past the point where
    # this build could answer it.
    if done is not None and kept and done < kept[-1][1]:
        kept = []
    if done is not None and (not kept or kept[-1] != (mtime, done)):
        kept.append((mtime, done))
    kept = [pair for pair in kept if mtime - pair[0] <= pp.RATE_WINDOW]
    try:
        path.write_text(json.dumps(kept[-SAMPLES_MAX:]))
    except OSError:
        pass
    return kept


def build_snapshot(repo: pathlib.Path, now: float):
    """`(snapshot, reattached)` for the build worth showing, or `(None, ...)`.

    Both status files are considered and the more recent one wins: a kernel
    rung and a package build do not run at once, but the file from the last
    one that did sticks around, and picking by name would show the older.

    THE ORPHAN CASE. A build outlives the porthole run that tracks it --
    `podman exec` runs it server-side, so a killed agent, a closed session or
    a timeout takes the tracker and not the work. The status file then freezes
    mid-build and `liveness` calls it stale, and the row vanished at exactly
    the moment somebody wanted it: this host spent two hours with a webkit
    build at 88% and a status line that said nothing at all.

    The log is the witness, the same one `porthole pkg watch` reattaches to.
    Here the test is only whether it was written seconds ago, because the
    status line cannot afford to ask the workspace anything -- and a log that
    is being appended to right now is a build that is running right now.
    """
    import porthole_progress as pp
    rundir = repo / ".run"
    best = None
    for name in ("build-status.json", "pkg-status.json"):
        snap = _snap(rundir, name)
        if not snap:
            continue
        if best is None or (snap.get("last_at") or 0) > (best.get("last_at") or 0):
            best = snap
    if best and pp.liveness(best) == "running":
        return best, False
    # ONLY when this checkout's own snapshot froze mid-build. The log belongs
    # to the machine's workspace, not to this repo, so a fresh one on its own
    # says "something is building somewhere" -- which in an unrelated
    # checkout is noise, and whose name could only be guessed. A frozen
    # `running` snapshot here is what makes the log's numbers this repo's
    # build, and makes its `rung` the right name for them.
    if best and (best.get("state") or "") == "running":
        live = _live_log(now)
        if live:
            text, _ = pp.log_tail(live[0])
            samples = _samples(repo / ".run", live[1],
                               pp.log_steps(text or "")[0], now)
            # The freshness rule itself lives in `porthole_progress`, shared
            # with `pkg watch` and `pkg status`: three copies of "is this
            # build still alive" would be three chances to disagree.
            snap = pp.reattach_from_log(live[0], best, now=now,
                                        samples=samples)
            # A reattached snapshot can say the build ENDED -- the log names
            # its own verdict -- and then it lingers like any other finished
            # run and then goes away. Without this the row kept a build that
            # failed overnight at `99% . reattached` all the next morning,
            # because every unrelated `pmbootstrap chroot` in the workspace
            # refreshed the one mtime that was keeping it on screen.
            if snap is not None and (
                    (snap.get("state") or "running") == "running"
                    or now - (snap.get("last_at") or 0) <= LINGER_S):
                return snap, True
    # Nothing this checkout published is running -- but something may still be
    # building. A `sandbox shell --command` build publishes no status file, and
    # neither does one that started after the last tracked build wrote `done`.
    # Both were invisible here while `pkg watch` showed them, because watch can
    # afford a podman `ps` and this cannot. The buildroot names the build and
    # the log's mtime says it is alive: two file reads, no container.
    live = _live_log(now)
    if live:
        import porthole_buildroot as buildroot
        name, started = buildroot.staged_build_name(live[0].parent)
        if name:
            text, _ = pp.log_tail(live[0])
            samples = _samples(repo / ".run", live[1],
                               pp.log_steps(text or "")[0], now)
            snap = pp.live_build_from_log(live[0], name, started, now=now,
                                          samples=samples)
            if snap is not None:
                return snap, True
    if best and now - (best.get("last_at") or 0) <= LINGER_S:
        return best, False       # old news lingers briefly; then it is clutter
    return None, False


def row_style():
    """Colour on, because this is chrome the harness paints rather than a
    pipe -- `detect_style` would see stdout is not a tty and turn it off.
    NO_COLOR is still honoured, and unicode still follows the encoding.
    """
    import porthole_progress as pp
    base = pp.detect_style()
    return pp.Style(colour=not os.environ.get("NO_COLOR"),
                    unicode=base.unicode)


def row_segments(snap, reattached: bool, width: int, now: float):
    """`[(text, tone)]` for the build row.

    Fewer fields than the watch block's -- one line of somebody's chrome is
    not the place for the rate and the elapsed -- but every number in it comes
    from the same `porthole_progress` primitives, so the two cannot disagree
    about what percentage this build is at.
    """
    import porthole_progress as pp
    state = snap.get("state") or "running"
    if state == "running" and snap.get("pid") is not None:
        state = pp.liveness(snap)
    name = str(snap.get("rung") or "build").split(":", 1)[-1]
    if state != "running":
        age = pp.fmt_dur(now - (snap.get("last_at") or now))
        return [(pp.spinner(snap, row_style()) + " ", "state"),
                (name, "name"), ("  ", "pad"), (state, "state"),
                ("  ", "pad"), (age + " ago", "dim")]
    out = [(name, "name"), ("  ", "pad"),
           (pp.bar(snap.get("progress"), _bar_width(width), row_style()),
            "state"), ("  ", "pad")]
    frac = snap.get("progress")
    out.append(("--" if frac is None else "{:>3d}%".format(int(frac * 100)),
                "figure"))
    if snap.get("steps"):
        out += [("  ", "pad"), (str(snap["steps"]), "dim")]
    if snap.get("eta"):
        out += [("  ", "pad"), ("eta ", "dim"),
                (pp.fmt_dur(snap["eta"]), "figure")]
    if reattached:
        out += [("  ", "pad"), ("\u00b7 reattached", "dim")]
    return out


def _bar_width(width: int) -> int:
    """The bar gives up its width first: a status line shares the row with
    somebody else's, and the numbers beside it are what carry the meaning."""
    return max(6, min(18, width // 5))


def build_line(repo: pathlib.Path, width: int, now=None):
    """The build row, or None when there is nothing worth a row."""
    import porthole_progress as pp
    now = time.time() if now is None else now
    snap, reattached = build_snapshot(repo, now)
    if not snap:
        return None
    style = row_style()
    state = snap.get("state") or "running"
    if state == "running" and snap.get("pid") is not None:
        state = pp.liveness(snap)
    colour = pp._STATE_COLOUR.get(state, "cyan")
    tones = {"name": "bold", "figure": "bold", "dim": "grey", "state": colour}
    segments = row_segments(snap, reattached, width, now)
    plain = "".join(text for text, _ in segments)
    if len(plain) > width:
        return pp.clip(plain, width)     # no colour on a line that was cut
    return "".join(pp.tint(text, tones[tone], style) if tone in tones else text
                   for text, tone in segments)


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
    import porthole_progress as pp
    style = row_style()
    bits = [pp.tint(model, "bold", style), pathlib.Path(cwd).name]
    if branch:
        bits.append(branch)
    if isinstance(pct, (int, float)):
        bits.append(pp.tint("{:.0f}% ctx".format(pct), "grey", style))
    return pp.tint(" · ", "grey", style).join(bits)


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
