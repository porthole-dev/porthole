# SPDX-License-Identifier: MIT
"""Build progress that does not lie.

WHY
    A build was a black box: minutes of raw kbuild output, or nothing. Agents
    filled the gap by inventing `sleep 60`, which is wrong in both directions --
    it wastes forty seconds when the build took twenty, and calls a failure when
    it needed ninety. brain/laws/poll-never-sleep.md says poll instead; it could
    not say poll WHAT, because nothing published progress.

THE HONEST PART
    A kernel build cannot tell you its total up front, and a bar that invents
    one is worse than no bar -- it reads as knowledge. So the totals come from
    HISTORY: what this rung's phases took last time, on this machine. The first
    run of a rung has no history and says `eta unknown` rather than guessing.

    Within `make`, the longest phase, the denominator is the previous run's
    compile-line count. That is the classic way to get a real fraction out of a
    job that will not declare its length, and it degrades to "no fraction, just
    elapsed" when there is nothing to compare against.

Everything here except the tracker is pure, so the arithmetic that produces a
user-facing ETA is testable without running a build.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time

# The phases the shell already announces, in the order they happen. Not every
# rung runs all of them -- `mod` stops after push -- and a phase that never
# arrives simply never contributes.
PHASES = ("make", "package", "install", "export", "verify", "flash", "push")

# kbuild's per-object lines. Counting these is what makes `make` a fraction
# rather than a spinner.
_COMPILE = re.compile(r"^\s*(CC|LD|AR|AS|CHK|GEN|OBJCOPY|MODPOST|HOSTCC)\b")

# ph-build.sh announces its own steps with `>>`. Cheaper and far more stable
# than inferring a phase from pmbootstrap's output, which changes between
# releases.
_MARKERS = (
    ("push", re.compile(r">>.*\b(push|insmod|reload)", re.I)),
    ("flash", re.compile(r">>.*\bflash", re.I)),
    ("verify", re.compile(r">>.*\bverif", re.I)),
    ("export", re.compile(r"pmbootstrap export|>>.*\bexport", re.I)),
    ("install", re.compile(r"pmbootstrap install|>>.*\binstall", re.I)),
    ("package", re.compile(r">>.*\b(package|apk|index)", re.I)),
)


def phase_of(line: str, current: str) -> str:
    """Which phase a line says we are in, or `current` if it says nothing.

    Order matters: `>> verifying the exported image` mentions both export and
    verify, and the later phase is the true one.
    """
    for name, pattern in _MARKERS:
        if pattern.search(line):
            return name
    if _COMPILE.match(line) and current == "":
        return "make"
    return current or ("make" if _COMPILE.match(line) else current)


def is_compile_line(line: str) -> bool:
    return bool(_COMPILE.match(line))


def fmt_dur(seconds) -> str:
    """`2m41s`, `18s`, `--` -- short enough to sit in a one-line status."""
    if seconds is None:
        return "--"
    seconds = int(max(0, seconds))
    if seconds < 60:
        return "{}s".format(seconds)
    return "{}m{:02d}s".format(seconds // 60, seconds % 60)


def bar(fraction, width: int = 18) -> str:
    """A bar, or an honest empty one when there is no fraction to draw."""
    if fraction is None:
        return "[" + "?" * width + "]"
    filled = int(max(0.0, min(1.0, fraction)) * width)
    return "[" + "=" * filled + ">" * (1 if filled < width else 0) + \
           " " * (width - filled - (1 if filled < width else 0)) + "]"


def estimate_total(history: dict, rung: str):
    """Seconds this rung took last time, or None when it has never run."""
    runs = (history or {}).get(rung) or {}
    total = runs.get("total")
    return total if isinstance(total, (int, float)) and total > 0 else None


def fraction(history: dict, rung: str, elapsed: float,
             compile_seen: int) -> float:
    """How far along, from whatever evidence exists.

    Prefers the compile count -- it moves smoothly and is not fooled by a phase
    that runs long -- and falls back to elapsed-against-last-total. Returns None
    when there is no history at all, which the bar renders as unknown rather
    than as zero.

    It also returns None once this build has OVERRUN the baseline, and that is
    the interesting case. The baseline is the last run of this rung, which may
    have been a small incremental build; a full rebuild blows past it in
    seconds. Clamping to 0.99 there reported "99%, eta 7s" for the fourteen
    remaining minutes of a 14m42s build. An exhausted baseline does not mean
    almost done, it means this run is not the run we measured -- unknown, the
    same answer as no history at all.
    """
    runs = (history or {}).get(rung) or {}
    lines = runs.get("compile_lines")
    if isinstance(lines, int) and lines > 0 and compile_seen > 0:
        return compile_seen / float(lines) if compile_seen < lines else None
    total = estimate_total(history, rung)
    if total:
        return elapsed / float(total) if elapsed < total else None
    return None


def eta(history: dict, rung: str, elapsed: float, frac):
    """Seconds remaining, or None when nothing supports a number.

    Deliberately refuses to extrapolate from a fraction under 5%: early in a
    build the ratio is noise, and a wildly wrong ETA is what teaches people to
    ignore the field.
    """
    if frac is not None and frac >= 0.05:
        return max(0.0, elapsed / frac - elapsed)
    total = estimate_total(history, rung)
    if total and elapsed < total:
        return total - elapsed
    return None


def load_history(rundir) -> dict:
    try:
        return json.loads((pathlib.Path(rundir) / "build-history.json").read_text())
    except (OSError, ValueError):
        return {}


def record(rundir, rung: str, total: float, compile_lines: int) -> None:
    """Remember what this rung cost, so the next run can predict it.

    Best effort: a build must never fail because bookkeeping did.
    """
    path = pathlib.Path(rundir) / "build-history.json"
    history = load_history(rundir)
    history[rung] = {"total": round(total, 1), "compile_lines": compile_lines,
                     "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(history, indent=2, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        pass


class Tracker:
    """Follows a build's output and publishes where it is.

    The status file is written continuously and atomically, because the whole
    point is that something else reads it WHILE the build runs -- an agent
    polling `porthole build --status --json` rather than sleeping.
    """

    def __init__(self, rundir, rung: str):
        self.rundir = pathlib.Path(rundir)
        self.rung = rung
        self.history = load_history(rundir)
        self.started = time.time()
        self.phase = ""
        self.compile_seen = 0
        self.last = ""
        self.state = "running"
        self._written = 0.0

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    def feed(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line.strip():
            return
        self.phase = phase_of(line, self.phase)
        if is_compile_line(line):
            self.compile_seen += 1
        self.last = line[:200]

    def snapshot(self) -> dict:
        frac = fraction(self.history, self.rung, self.elapsed, self.compile_seen)
        return {"rung": self.rung, "phase": self.phase or "starting",
                "state": self.state, "pid": os.getpid(),
                "elapsed": round(self.elapsed, 1),
                "progress": None if frac is None else round(frac, 3),
                "eta": eta(self.history, self.rung, self.elapsed, frac),
                "compile_lines": self.compile_seen,
                "last": self.last,
                "started": round(self.started, 1)}

    def publish(self, force: bool = False) -> None:
        # Twice a second is enough for a human eye and cheap enough that a
        # build producing thousands of lines does not spend its time on IO.
        if not force and time.time() - self._written < 0.5:
            return
        self._written = time.time()
        path = self.rundir / "build-status.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.snapshot(), indent=2))
            os.replace(tmp, path)
        except OSError:
            pass

    def finish(self, ok: bool) -> None:
        self.state = "done" if ok else "failed"
        self.publish(force=True)
        if ok:
            record(self.rundir, self.rung, self.elapsed, self.compile_seen)

    def line(self, width: int = 18) -> str:
        """The one-line human view."""
        snap = self.snapshot()
        pct = "--" if snap["progress"] is None else \
            "{:>3d}%".format(int(snap["progress"] * 100))
        return "{} {} {:<8} {:>7} eta {:>7}".format(
            bar(snap["progress"], width), pct, snap["phase"],
            fmt_dur(snap["elapsed"]), fmt_dur(snap["eta"]))
