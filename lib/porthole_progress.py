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


# ---------------------------------------------------------------- packages --
#
# A package build is the case the kernel rungs could not have: ninja prints
# `[N/M]`, so the denominator is REAL. Everything above this line has to infer
# a total from history because kbuild will not declare one; here the build
# says it outright, and a fraction from a stated total is not a guess.
#
# cmake's configure step is the exception and it is why `configure` is its own
# phase. It emits no `[N/M]` for ~90 s on webkit, so without a phase of its own
# the bar sits at 0% looking hung -- the single most common reason someone
# kills a build that was working.
_NINJA = re.compile(r"^\[(\d+)/(\d+)\]")

# pmbootstrap prefixes EVERY line it relays with `[HH:MM:SS] `. Found by
# running it: without stripping this, `[4567/9999] Building CXX` arrives as
# `[11:28:24] [4567/9999] ...`, the anchored ninja match never fires, and the
# "real percentage" this whole module claims for package builds is silently
# always None. The bar would have sat at unknown for the entire webkit build
# and nothing would have said why.
_STAMP = re.compile(r"^\[\d\d:\d\d:\d\d\]\s*")

# Reverse chronological, same rule as _MARKERS: the first pattern that matches
# wins, so a line mentioning two phases resolves to the later one.
_PKG_MARKERS = (
    ("index", re.compile(r">>>.*\bindex\b|Updating the index", re.I)),
    ("package", re.compile(
        r">>>.*\b(fakeroot|split function|subpackage|tracing dependencies|"
        r"package size|compressing|create checksum|build complete)", re.I)),
    # pmbootstrap's own `=>` progress lines, not just abuild's `>>>`. A device
    # package never reaches abuild's markers in the relayed output at all, so
    # without these the phase stays "starting" for the whole build.
    ("build", re.compile(r"^\s*\[\d+/\d+\]|^\s*(CC|CXX|LD|AR)\s|"
                         r"=>.*\bBuilding package\b", re.I)),
    ("configure", re.compile(
        r"^\s*-- |\bconfiguring done\b|\bmeson\.build\b|"
        r"\bRun-time dependency\b", re.I)),
    ("fetch", re.compile(
        r">>>.*\b(fetching|sha512sum|unpacking|checking sanity)", re.I)),
    ("deps", re.compile(
        r">>>.*\b(analyzing dependencies|installing for build|"
        r"cleaning temporary)|=>.*\bInstalling dependencies\b|"
        r"^\((native|buildroot_[a-z0-9_]+)\) install\b", re.I)),
)


def ninja_progress(line: str):
    """`(done, total)` from a `[N/M]` line, or None.

    Anchored: `[3/4]` inside a compiler diagnostic is not progress, and a
    build that reports someone else's numbers is worse than one that reports
    none.
    """
    match = _NINJA.match(_STAMP.sub("", line.lstrip()))
    if not match:
        return None
    done, total = int(match.group(1)), int(match.group(2))
    return (done, total) if total > 0 and done <= total else None


def pkg_phase_of(line: str, current: str) -> str:
    """Which phase of a package build a line says we are in."""
    line = _STAMP.sub("", line.lstrip())
    for name, pattern in _PKG_MARKERS:
        if pattern.search(line):
            return name
    return current


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

    # Which file this tracker publishes to. A package build must not overwrite
    # the kernel build's status: they are different runs and both are worth
    # polling, and one clobbering the other is how `status` starts describing
    # a build nobody asked about.
    status_name = "build-status.json"

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

    def _fraction(self):
        """Where this tracker's percentage comes from.

        A seam, not indirection for its own sake: a package build has a real
        denominator from ninja and a kernel build does not, and that is the
        ONLY thing that differs between them. Overriding one method keeps the
        publish/history/log path a single implementation.
        """
        return fraction(self.history, self.rung, self.elapsed, self.compile_seen)

    def snapshot(self) -> dict:
        frac = self._fraction()
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
        path = self.rundir / self.status_name
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
        return line_of(self.snapshot(), width)


class PkgTracker(Tracker):
    """A package build, whose percentage is stated rather than inferred.

    Differs from the kernel tracker in exactly two ways: the phase markers are
    abuild's and cmake's rather than ph-build.sh's, and the fraction comes from
    ninja's `[N/M]` when ninja has started. Everything else -- the atomic
    status file, the history, the ETA arithmetic, the one-line render -- is the
    parent's, because a second copy of that is precisely what this was supposed
    to avoid.
    """

    status_name = "pkg-status.json"

    def __init__(self, rundir, rung: str):
        super().__init__(rundir, rung)
        self.ninja_total = 0

    def feed(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line.strip():
            return
        self.phase = pkg_phase_of(line, self.phase)
        step = ninja_progress(line)
        if step:
            self.compile_seen, self.ninja_total = step
        self.last = line[:200]

    def _fraction(self):
        # ninja's own numbers first: they are the total the build declared, so
        # this is the one place in the file that reports a fraction without
        # needing a previous run to compare against.
        if self.ninja_total > 0:
            return min(1.0, self.compile_seen / float(self.ninja_total))
        # Before ninja starts -- dependency install, fetch, cmake configure --
        # there is no denominator. If this aport has been built here before,
        # elapsed-against-last-total is honest; otherwise it stays unknown and
        # the bar says so rather than sitting at 0%.
        return fraction(self.history, self.rung, self.elapsed, 0)


def line_of(snap, width: int = 18) -> str:
    """The one-line view, from a snapshot rather than from a live tracker.

    Module-level so that something following the status FILE renders exactly
    what the build's own terminal renders. Two implementations of this line is
    how a watcher ends up disagreeing with the thing it is watching.
    """
    frac = snap.get("progress")
    pct = "--" if frac is None else "{:>3d}%".format(int(frac * 100))
    return "{} {} {:<9} {:>7} eta {:>7}".format(
        bar(frac, width), pct, snap.get("phase") or "starting",
        fmt_dur(snap.get("elapsed")), fmt_dur(snap.get("eta")))


def publish_pending(rundir, rung: str, pid: int,
                    name: str = "pkg-status.json") -> None:
    """Stake the status file for a run that has started but not yet spoken.

    Without this, `watch` run immediately after `build --detach` reads the
    PREVIOUS build's finished snapshot and reports it as the answer -- which
    is the same "a stale run reads as the current one" defect this module
    exists to prevent, reintroduced through the back door by the detach path.
    Measured: watching a fresh gst-plugins-good build printed phoc's result
    from two minutes earlier and exited.

    The child overwrites this within a second or two. It only has to be true
    for that window, and `running` with the child's pid is true.
    """
    snap = {"rung": rung, "phase": "starting", "state": "running", "pid": pid,
            "elapsed": 0.0, "progress": None, "eta": None,
            "compile_lines": 0, "last": "", "started": round(time.time(), 1)}
    path = pathlib.Path(rundir) / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, indent=2))
        os.replace(tmp, path)
    except OSError:
        pass


def finished_at(snap):
    """When a run stopped, or None if the snapshot cannot say."""
    started, elapsed = snap.get("started"), snap.get("elapsed")
    if isinstance(started, (int, float)) and isinstance(elapsed, (int, float)):
        return started + elapsed
    return None


# ------------------------------------------------------------- liveness --
#
# `status` used to render a bar and an ETA straight out of the snapshot, so a
# run that died hours ago kept printing `[>      ] starting ... eta 6s` as
# though it were in flight. `state failed` was in there, but a moving-looking
# bar beats a word every time. The rule is: a bar is for a run that is
# actually running, and whether it is running is a question about the pid.


def pid_alive(pid) -> bool:
    """Is that process still there? Signal 0 checks without delivering."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists and belongs to someone else. Alive is the honest answer;
        # "gone" would be a lie that happens to be convenient.
        return True
    except OSError:
        return False
    return True


def liveness(snap, alive=None) -> str:
    """`running` | `done` | `failed` | `stale`.

    `stale` is the case that motivated this: the file says running because
    nothing ever wrote a final state -- the process was killed, the machine
    rebooted, the terminal went away -- and only the pid can tell you.
    """
    state = (snap or {}).get("state") or "unknown"
    if state != "running":
        return state
    check = pid_alive if alive is None else alive
    return "running" if check((snap or {}).get("pid")) else "stale"


_HEADLINE = {"done": "finished", "failed": "FAILED",
             "stale": "no longer running -- its process is gone"}


def status_report(snap, alive=None, now=None):
    """`(headline, rows)` for a `status` verb. Pure, so the staleness rule is
    testable without running a build.

    A bar is drawn only for a run that is live. For anything else the headline
    says what happened and how long ago, because "when" is the field whose
    absence let a dead build look current for half an hour.
    """
    live = liveness(snap, alive)
    now = time.time() if now is None else now
    elapsed = snap.get("elapsed")
    rows = [("target", snap.get("rung", "?")), ("state", live),
            ("elapsed", fmt_dur(elapsed))]
    if live == "running":
        head = "{} {}".format(bar(snap.get("progress")),
                              snap.get("phase") or "starting")
        rows.append(("eta", fmt_dur(snap.get("eta"))))
    else:
        head = _HEADLINE.get(live, live)
        started = snap.get("started")
        if isinstance(started, (int, float)) and isinstance(elapsed, (int, float)):
            head += "  {} ago".format(fmt_dur(now - (started + elapsed)))
        rows.append(("phase", snap.get("phase") or "?"))
    if snap.get("last"):
        rows.append(("last", str(snap["last"])[:100]))
    return head, rows
