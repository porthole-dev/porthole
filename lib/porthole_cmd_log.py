# SPDX-License-Identifier: MIT
"""`porthole log` -- read and rotate the build logs nobody was collecting.

`porthole build` and `porthole pkg` write every line of every run to
`.run/<prefix>-<rung>-<stamp>.log` unconditionally (porthole_cmd_build._stream)
and nothing ever read them back or cleaned them up: 255+ files, 131 MB, back
to 2026-08-29, on the host this was measured against. When a build fails the
advice is "the log has more" -- and finding which of 255 files that is was
left to the reader.

READ-ONLY BY DEFAULT
    A bare `porthole log` only lists. Deleting anything -- even a log well
    past the keep window -- requires the explicit `--prune --yes` pair, same
    rule `porthole disk` follows: a report and a destructive action are two
    different commands wearing one verb, never one command that sometimes
    does the other.

WHY keep() IS PURE
    The policy ("which logs survive") is one function that takes filenames
    and returns a decision -- no filesystem, no clock but the one passed in.
    That is what makes `tests/test_log.py` able to assert on 255 fabricated
    names without writing 131 MB of fixtures to disk.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
from datetime import datetime, timedelta

from porthole_cli import Bail, EX_FAIL

# The stamp `_stream` names every log with: `time.strftime("%Y%m%d-%H%M%S")`,
# always the last thing before `.log` (porthole_cmd_build._stream). Matching
# it from the END rather than assuming a fixed field count is what survives a
# rung slug that itself contains digits and dashes (`pkg-webkit2gtk-6.0`).
_STAMP_RE = re.compile(r"(\d{8}-\d{6})\.log$")

# `build-auto-20260908-120000.log` -> `auto`; `pkg-webkit2gtk-6.0-...` ->
# `webkit2gtk-6.0`. The prefix is always the first `-`-separated token
# (`build` or `pkg`, porthole_cmd_build._stream's `log_prefix`); the rung slug
# is everything between that and the stamp this same regex just found.
_NAME_RE = re.compile(r"^[^-]+-(.+)-\d{8}-\d{6}\.log$")


def _stamp(name: str):
    m = _STAMP_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def rung_of(name: str) -> str:
    """The rung slug embedded in a log's filename, or "" if it does not fit
    the shape `_stream` writes (a detached spawn log, say). Pure."""
    m = _NAME_RE.match(name)
    return m.group(1) if m else ""


def keep(names, max_count=50, max_age_days=14, now=None, active=None):
    """Which of these log filenames survive rotation. Pure -- (keep, drop).

    Two independent caps, both must hold to survive: rank in the newest
    `max_count`, AND no older than `max_age_days`. `active` -- the log the
    running build is writing right now -- is exempt from both: rotating out
    the only record of the thing you are watching is worse than the disk it
    saves (see test_a_log_the_running_build_is_writing_is_never_dropped).

    A name with no parseable `YYYYMMDD-HHMMSS` stamp cannot have its age
    judged, so it sorts as oldest and loses ties for a `max_count` slot --
    conservative, not silently exempt from rotation forever.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(days=max_age_days)
    ordered = sorted(names, key=lambda n: _stamp(n) or datetime.min,
                     reverse=True)

    kept, dropped = [], []
    for rank, name in enumerate(ordered):
        stamp = _stamp(name)
        survives = (name == active
                    or (rank < max_count
                        and stamp is not None and stamp >= cutoff))
        (kept if survives else dropped).append(name)
    return kept, dropped


# ------------------------------------------------------------------ shell --

def _rundir(ctx) -> pathlib.Path:
    return pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))


def _rows(rundir: pathlib.Path):
    """[(name, mtime, size)] for every *.log under rundir. Newest first."""
    rows = []
    for path in rundir.glob("*.log"):
        try:
            st = path.stat()
        except OSError:
            continue
        rows.append((path.name, st.st_mtime, st.st_size))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def _active_log(rundir: pathlib.Path) -> str:
    """The log the RUNNING build is writing, or "" if nothing is running.

    porthole serialises builds through one lock (`hold()` in
    porthole_cmd_build), so at most one can ever be active -- it is the
    newest log on disk, the moment build-status.json says a build is
    genuinely running (not a stale file left by a crash).

    ponytail: the status snapshot does not record which log it is writing,
    so this infers it from "newest .log while running". Upgrade path: have
    `_stream` write its own logpath into the snapshot, if builds ever stop
    being serialised.
    """
    import porthole_progress as progress

    try:
        snap = json.loads((rundir / "build-status.json").read_text())
    except (OSError, ValueError):
        return ""
    if progress.liveness(snap) != "running":
        return ""
    rows = _rows(rundir)
    return rows[0][0] if rows else ""


def _ago(seconds) -> str:
    """Like progress.fmt_dur, but does not top out at hours -- these logs
    run back over a week and `192h00m` is not a useful thing to read."""
    import porthole_progress as progress

    if seconds >= 86400:
        return "{}d{}h".format(int(seconds // 86400),
                               int(seconds % 86400 // 3600))
    return progress.fmt_dur(seconds)


def _fmt_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}G"


_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)([smhd])$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """`"2d"`, `"6h"`, `"90m"` -> seconds. Pure."""
    m = _DURATION_RE.match((text or "").strip())
    if not m:
        raise Bail(f"not a duration: {text!r}", EX_FAIL,
                   "use a number and one of s/m/h/d, e.g. --since 2d")
    return float(m.group(1)) * _DURATION_UNITS[m.group(2)]


def _follow(path: pathlib.Path, out) -> int:
    with open(path, "r", errors="replace") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if line:
                out(line.rstrip("\n"))
            else:
                time.sleep(0.5)


def cmd_log(args, ctx) -> int:
    rundir = _rundir(ctx)
    active = _active_log(rundir)

    if args.follow:
        if not active:
            raise Bail("no build is currently running", EX_FAIL,
                       "porthole build status -- see what last ran")
        if args.rung and args.rung not in active:
            raise Bail(f"the running build is not {args.rung!r}", EX_FAIL,
                       f"it is writing {active}")
        ctx.out(f"following {rundir / active}")
        return _follow(rundir / active, ctx.out)

    rows = _rows(rundir)
    if args.rung:
        rows = [r for r in rows if args.rung in rung_of(r[0])]
    if args.since:
        cutoff = time.time() - parse_duration(args.since)
        rows = [r for r in rows if r[1] >= cutoff]
    rows = rows[:args.last]

    if args.prune:
        if not args.yes:
            raise Bail("--prune deletes logs -- pass --yes too", EX_FAIL,
                       "porthole log --prune --yes")
        names = [r[0] for r in _rows(rundir)]
        kept, dropped = keep(names, args.max_count, args.max_age_days,
                             active=active)
        reclaimed = 0
        for name in dropped:
            path = rundir / name
            try:
                reclaimed += path.stat().st_size
                path.unlink()
            except OSError:
                pass
        payload = {"kept": len(kept), "dropped": dropped,
                   "reclaimed_bytes": reclaimed}
        return ctx.emit(payload, lambda: ctx.out(
            f"dropped {len(dropped)} logs, kept {len(kept)}, "
            f"reclaimed {_fmt_size(reclaimed)}"))

    payload = {
        "active": active,
        "logs": [{"name": n, "rung": rung_of(n), "age_s": time.time() - m,
                  "bytes": s, "active": n == active} for n, m, s in rows],
    }

    def render():
        if not rows:
            ctx.out(f"no logs in {rundir}")
            return
        for name, mtime, size in rows:
            mark = "  [active]" if name == active else ""
            ctx.out(f"  {_ago(time.time() - mtime):>7}  {_fmt_size(size):>6}  "
                    f"{rung_of(name) or '?':<12}  {name}{mark}")
        all_names = [r[0] for r in _rows(rundir)]
        _, dropped = keep(all_names, args.max_count, args.max_age_days,
                          active=active)
        if dropped:
            ctx.out.hint("porthole log --prune --yes",
                         f"{len(dropped)} logs are past the keep window")

    return ctx.emit(payload, render)


SPEC = {
    "verb": "log",
    "order": 38,
    "group": "build",
    "help": "list, follow and rotate the build logs .run/ has been "
            "accumulating",
    "description": (
        "Every `porthole build` and `porthole pkg` run writes a full log to\n"
        ".run/ and nothing ever read them back. `porthole log` lists them,\n"
        "follows the one an in-progress build is writing, and -- only with\n"
        "--prune --yes -- rotates the rest away."),
    # NOT escapes_scope: .run/ is gitignored run state inside PORTHOLE_ROOT,
    # the same category porthole_cmd_experiment.py's --allow-dirty is in.
    # That flag is for a verb writing INTO a chroot, onto a device, or into
    # pmaports -- none of which --prune touches.
    "args": [
        (["--last"], {"type": int, "default": 20, "metavar": "N",
                      "help": "show only the N most recent logs"}),
        (["--follow"], {"action": "store_true",
                        "help": "tail the log the running build is writing"}),
        (["--rung"], {"metavar": "RUNG",
                      "help": "only this rung's logs"}),
        (["--since"], {"metavar": "DURATION",
                       "help": "only logs from within this long ago, "
                               "e.g. 2d, 6h"}),
        (["--prune"], {"action": "store_true",
                       "help": "delete logs past the keep window "
                               "(destructive; needs --yes)"}),
        (["--max-count"], {"type": int, "default": 50, "metavar": "N",
                           "help": "how many logs --prune keeps (default 50)"}),
        (["--max-age-days"], {"type": int, "default": 14, "metavar": "N",
                              "help": "how old --prune lets a log get "
                                      "(default 14)"}),
        (["--yes"], {"action": "store_true",
                     "help": "confirm --prune actually deletes files"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_log,
    "examples": [
        "porthole log",
        "porthole log --follow",
        "porthole log --rung fast --since 2d",
        "porthole log --prune --yes",
    ],
}
