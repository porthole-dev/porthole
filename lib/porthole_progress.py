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

import collections
import json
import os
import pathlib
import re
import time

from porthole_cli import Bail, EX_FAIL, EX_OK

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
    # pmbootstrap says outright when there was nothing to do. Recording it
    # is what makes a two-second build coherent: without this the status read
    # `elapsed 2s, phase none reached, last DONE!` and left you to work out
    # why it was two seconds, while the log's FIRST line had already said so
    # and only the last line was kept.
    ("up-to-date", re.compile(r"\bis up to date\b|\bNothing to do\b", re.I)),
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


# What ninja says it is DOING, which decides whether the step counts towards a
# rate. A `Generating` step is one emulated Ruby or Perl process holding one
# core; counting it as progress is what produced "eta 74h" on a healthy build.
_STEP_KIND = (
    ("compile", re.compile(r"\b(Building|Compiling)\b", re.I)),
    ("link", re.compile(r"\b(Linking|Archiving|Indexing)\b", re.I)),
    ("generate", re.compile(r"\b(Generating|Copying|Creating)\b", re.I)),
)


def step_kind(line: str) -> str:
    """`compile` | `link` | `generate` | `other` for one ninja step line."""
    for name, pattern in _STEP_KIND:
        if pattern.search(line):
            return name
    return "other"


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
    if seconds < 3600:
        return "{}m{:02d}s".format(seconds // 60, seconds % 60)
    # webkit is measured in hours. `134m10s` is a number you have to do
    # arithmetic on before it means anything.
    return "{}h{:02d}m".format(seconds // 3600, (seconds % 3600) // 60)


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
        # (when, compile_seen) so BOTH trackers can measure a real rate. The
        # kernel rungs used to extrapolate elapsed-against-last-total, which
        # has the same failure mode the package builds measured: a long
        # single-threaded step makes the estimate grow without bound.
        self._samples = collections.deque(maxlen=4096)

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
            self._samples.append((time.time(), self.compile_seen))
        self.last = line[:200]

    def _fraction(self):
        """Where this tracker's percentage comes from.

        A seam, not indirection for its own sake: a package build has a real
        denominator from ninja and a kernel build does not, and that is the
        ONLY thing that differs between them. Overriding one method keeps the
        publish/history/log path a single implementation.
        """
        return fraction(self.history, self.rung, self.elapsed, self.compile_seen)

    def _eta(self, frac):
        """Seconds remaining, from a measured rate where one exists.

        A seam, like _fraction. The windowed rate comes first because the
        history path extrapolates elapsed-against-last-total, and that grows
        without bound through a long single-threaded step -- the package
        builds measured it producing a 74-hour estimate on a healthy build.
        When there are too few samples to divide by, the history path is
        still the best available answer, so it remains the fallback rather
        than being replaced.
        """
        recent = window_rate(self._samples, time.time())
        total = (self.history or {}).get(self.rung, {}).get("compile_lines")
        if recent and isinstance(total, int) and total > self.compile_seen:
            return (total - self.compile_seen) / recent
        return eta(self.history, self.rung, self.elapsed, frac)

    def snapshot(self) -> dict:
        frac = self._fraction()
        # The RAW phase, which may be "". "starting" is a rendering default
        # and storing it made a finished run carry `phase: starting` forever:
        # a two-second build whose only output was `DONE!` matched no phase
        # marker, so the field never advanced, and `state: done` was printed
        # next to `phase: starting`. Data stays honest; renderers default.
        return {"rung": self.rung, "phase": self.phase,
                "state": self.state, "pid": os.getpid(),
                "elapsed": round(self.elapsed, 1),
                "progress": None if frac is None else round(frac, 3),
                "eta": self._eta(frac),
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


# How a package build's ETA is allowed to be computed. Every one of these
# numbers exists because the naive version was measured lying:
#
#   ccache replay      the restarted webkit run went 0% -> 32% in fifteen
#                      minutes because ccache replayed 2658 already-compiled
#                      objects. An ETA over that window measures cache
#                      lookups, not compilation.
#   generator stalls   sampled mid-build, the counter moved five steps in 240s
#                      while one emulated Ruby process ran JavaScriptCore's
#                      offlineasm with fifteen cores idle. Extrapolating that
#                      says 74 HOURS remaining on a healthy build, and an
#                      agent watching that number kills the build.
#
# See docs/HANDOFF-package-builds.md, "[N/M] alone will lie".
RATE_WINDOW = 180.0    # trailing seconds the rate is measured over
RATE_MIN_SPAN = 60.0   # ...and the least of it worth dividing by
RATE_MIN_STEPS = 8     # ...and the fewest compiles in it worth trusting
ETA_WARMUP = 180.0     # no ETA at all before this; the start is all cache


def window_rate(samples, now: float, window: float = RATE_WINDOW):
    """Compile steps per second over the trailing window, or None.

    None means "no defensible rate", which is a different statement from zero
    and is rendered as `--` rather than as an ETA. A stall produces None here
    rather than a very large ETA downstream, which is the whole point: the
    honest answer to "how long is this generator step" is that we do not know.
    """
    recent = [(when, done) for when, done in samples if when >= now - window]
    if len(recent) < 2:
        return None
    span = recent[-1][0] - recent[0][0]
    steps = recent[-1][1] - recent[0][1]
    if span < RATE_MIN_SPAN or steps < RATE_MIN_STEPS:
        return None
    return steps / span


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
        self.step = ""
        self.compiles = 0

    def feed(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line.strip():
            return
        self.phase = pkg_phase_of(line, self.phase)
        step = ninja_progress(line)
        if step:
            self.compile_seen, self.ninja_total = step
            self.step = step_kind(line)
            if self.step == "compile":
                self.compiles += 1
                self._samples.append((time.time(), self.compiles))
        self.last = line[:200]

    def rate(self):
        """Compiles per second right now, or None when nothing supports one."""
        return window_rate(self._samples, time.time())

    def _eta(self, frac):
        """Remaining steps divided by a rate we can defend, or None.

        Deliberately NOT `elapsed / fraction - elapsed`, which is what the
        kernel rungs use and what produced the 74-hour number: that treats
        every second so far as representative, and on these builds it is not
        -- the first fifteen minutes are ccache replay and the middle contains
        single-threaded generator steps with fifteen cores idle.
        """
        if self.elapsed < ETA_WARMUP or self.ninja_total <= 0:
            return None
        recent = self.rate()
        if not recent:
            return None
        # The slower of the recent window and the run so far. After a ccache
        # replay the sustained rate is inflated by thousands of cache hits, so
        # the minimum keeps the estimate from inheriting that optimism once
        # real compilation starts. Pessimistic is the right direction to err:
        # an ETA that shortens is a pleasant surprise, one that grows tenfold
        # is what teaches people to ignore the field.
        #
        # Measured from the FIRST COMPILE, not from the start of the build.
        # Dividing by total elapsed would fold in dependency install, fetch and
        # a six-minute cmake configure -- none of which compiled anything --
        # and report a compile rate several times lower than the real one.
        best = recent
        if len(self._samples) >= 2:
            first_at, first_n = self._samples[0]
            span = time.time() - first_at
            if span > 0:
                sustained = (self.compiles - first_n) / span
                if sustained > 0:
                    best = min(recent, sustained)
        remaining = max(0, self.ninja_total - self.compile_seen)
        return remaining / best if best > 0 else None

    def snapshot(self) -> dict:
        snap = super().snapshot()
        recent = self.rate()
        snap["rate"] = None if recent is None else round(recent, 2)
        snap["step"] = self.step
        snap["steps"] = f"{self.compile_seen}/{self.ninja_total}" \
            if self.ninja_total else ""
        return snap

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
    # A ninja step that is Generating rather than Building is shown as such.
    # It is the single most useful thing on this line during a stall: the
    # counter is not moving, and "generating" says that is expected while
    # "build" invites the reader to conclude the thing has hung.
    phase = snap.get("phase") or "starting"
    phase = {"generate": "generating", "link": "linking"}.get(
        snap.get("step"), phase) if phase == "build" else phase
    rate = snap.get("rate")
    # Rate is shown for package builds and omitted for kernel rungs, which
    # have no step count to have a rate over.
    tail = "" if "rate" not in snap else \
        " {:>6}".format("--/s" if not rate else "{:.1f}/s".format(rate))
    return "{} {} {:<10}{} {:>7} eta {:>7}".format(
        bar(frac, width), pct, phase, tail,
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
    snap = {"rung": rung, "phase": "", "state": "running", "pid": pid,
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
        # Not "starting": this run is over. A build that reached no phase at
        # all -- because it had nothing to do -- says so, rather than claiming
        # it is about to begin.
        #
        # A literal "starting" is treated the same way, because status files
        # written before snapshot() stopped storing that default still carry
        # it, and "starting" on a finished run is meaningless however it got
        # there. Without this the fix would only take effect on the NEXT
        # build, and the reported symptom would still be on screen.
        reached = snap.get("phase") or ""
        rows.append(("phase", "none reached" if reached in ("", "starting")
                     else reached))
    if snap.get("last"):
        rows.append(("last", str(snap["last"])[:100]))
    return head, rows


# --------------------------------------------------------------- watching --
#
# Moved here from `porthole_cmd_pkg` (Task 13) so `porthole build watch`
# (Task 14) can be the SAME implementation rather than a second one that
# drifts. `porthole pkg watch` carries four decisions, each paid for in a
# real session, and every one survives the move:
#   1. No ceiling on a tty, a ceiling off one (`wait_ceiling`).
#   2. Never silent -- it says what it found BEFORE the first sleep and
#      keeps saying it, never the old bare `continue`'s blank screen.
#   3. A run that finished before this watch began is somebody else's, and
#      is reported as the PREVIOUS run, not as the answer (`is_stale`).
#   4. It polls a real signal rather than sleeping through the build --
#      brain/laws/poll-never-sleep.md.


def wait_ceiling(tty: bool, now: float, seconds: float = 30.0):
    """When to stop waiting for a run to start, or None for never.

    None on a terminal: `watch` is advertised as free to leave open, and a
    ceiling defeats that -- opened before an agent starts a build, it would
    exit before the build began. A person can Ctrl-C. A pipe cannot, and an
    agent that ran this by accident would hang forever, so it keeps a ceiling.
    """
    return None if tty else now + seconds


def waiting_line(snap, now=None) -> str:
    """What `watch` is doing while there is nothing live to attach to.

    Said once immediately and then repeated, never left to silence. Measured:
    `timeout 5 porthole pkg watch` against an already-finished build printed
    NOTHING, because the old code's grace window was a bare `continue` --
    thirty seconds of blank screen that reads as a hang, not as "waiting".
    Pure (a snapshot and a clock, no file, no sleep) so the wording is
    testable without driving the loop.
    """
    now = time.time() if now is None else now
    if not snap:
        return "  nothing has built in this checkout yet -- waiting for a build to start"
    stopped = finished_at(snap)
    ago = fmt_dur(now - stopped) if stopped is not None else "a while"
    return (f"  {snap.get('rung', '?')} finished {ago} ago -- "
            f"waiting for a new build to start")


# The key set every `snapshot()` (Tracker's and PkgTracker's on-disk shape,
# and `publish_pending`'s) always includes. Used as the ndjson "waiting"
# object's baseline: copy a previous snapshot's own keys when there is one,
# fall back to this -- all `None` -- when nothing has EVER published here, so
# the emitted object's key set never depends on which branch produced it.
NDJSON_KEYS = ("rung", "phase", "state", "pid", "elapsed", "progress", "eta",
              "compile_lines", "last", "started")


def watch(rundir, status_name: str, interval: float, out, ndjson: bool = False,
          tty=None, start_hint: str = "") -> int:
    """Follow a status file until the run stops. Returns EX_OK when the run
    finished `done`, non-zero otherwise.

    This exists so that watching a run costs NOTHING. A human leaves this
    open in a second terminal and gets the same bar the run prints; an agent
    never has to poll, because it can start the run as a background job and
    be told when it exits. The failure mode this replaces is an agent burning
    a request every thirty seconds to re-read a number that changed by 1%.

    `out` is a LINE SINK (one positional argument, e.g. `print`) rather than
    a terminal, so this loop is testable without a tty and reusable by any
    verb that publishes a status file shaped like `porthole_progress`'s.
    Every line this function emits carries its own line ending -- a bare
    `\\r\\033[2K` prefix (no trailing newline) for a tty redraw-in-place, a
    trailing `\\n` for everything else -- so `out` itself never has to know
    which mode is active.

    `ndjson=True` emits one JSON object per update instead of a bar, and
    skips the final `status_report` block: an agent can consume a stream: it
    cannot consume a redrawn terminal.

    ONE SHAPE, always -- this was a discriminated union (a waiting object with
    only `state`/`note`/`previous`, a live object with the full snapshot) and
    an agent doing `json.loads(line)["rung"]` KeyErrored on line one, in
    exactly the "somebody else's run just finished" case this feature exists
    to handle. Every emitted object now carries the same key set --
    `rung`, `phase`, `state`, `pid`, `elapsed`, `progress`, `eta`,
    `compile_lines`, `last`, `started` (plus `rate` when the tracker reports
    one) -- so `obj["rung"]`, `obj["progress"]`, `obj["state"]` are always
    safe to read. `state` is still the discriminator: `"waiting"` means no
    run is live right now (a `note` key carries the human sentence, and the
    rest of the fields are the PREVIOUS run's, or all `None` if nothing has
    ever published here); anything else is a real snapshot's own `state`
    (`"running"`, `"done"`, `"failed"`, ...). A `None` value means genuinely
    not known yet, never a missing key.

    `start_hint`, if given, is the exact command that starts a run of this
    kind (e.g. "porthole pkg build <aport>") -- only the CALLER knows that,
    so this function never guesses one from `status_name`. Without it, the
    "nothing has ever published here" error falls back to generic wording.

    Polling a file, not sleeping through the run: the sleep here is between
    reads of a real signal, which is what brain/laws/poll-never-sleep.md asks
    for rather than what it forbids.
    """
    path = pathlib.Path(rundir) / status_name
    if tty is None:
        tty = os.isatty(1)

    def snapshot():
        # Absent, or read mid-rename: both are "nothing to attach to yet",
        # not an error -- the writer is atomic, so the next read succeeds.
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    started_watching = time.time()
    # How long to wait for a run to START, and it depends on who is
    # watching. `watch` is advertised as "costs nothing to leave open", and a
    # ceiling breaks exactly that use: open it in a second terminal BEFORE the
    # agent kicks off a run and it gives up before the run begins.
    #
    # So on a terminal there is no ceiling -- a person left it open on purpose
    # and can Ctrl-C. Off a terminal there is one, because a pipe cannot be
    # interrupted meaningfully and an agent that ran this by mistake would
    # hang forever; it gets the previous run and an exit instead.
    appear = wait_ceiling(tty, started_watching)

    def is_stale(snap):
        # A run that had already finished before this watch began is
        # somebody else's run. Keep waiting for ours rather than reporting
        # theirs -- belt and braces behind publish_pending, for a `watch`
        # started by hand rather than straight after `--detach`.
        if not snap:
            return False
        stopped = finished_at(snap)
        return (liveness(snap) != "running" and stopped is not None
                and stopped < started_watching)

    # Never silent: say what was found BEFORE the first sleep, then keep
    # saying it (repainted in place on a tty, throttled on a pipe or as
    # ndjson) for as long as there is nothing live -- instead of the old bare
    # `continue`.
    last_note = 0.0
    snap = snapshot()
    while (snap is None or is_stale(snap)) and (appear is None
                                                or time.time() < appear):
        line = waiting_line(snap)
        if ndjson:
            if last_note == 0.0 or time.time() - last_note > max(interval, 15):
                last_note = time.time()
                # Same key set a live object has (copied from the previous
                # run's own snapshot when there is one), not a bare
                # {state, note} pair -- see the ONE SHAPE note above.
                obj = dict(snap) if snap else dict.fromkeys(NDJSON_KEYS)
                obj["state"] = "waiting"
                obj["note"] = line
                out(json.dumps(obj) + "\n")
        elif tty:
            out("\r\033[2K" + line)
        elif last_note == 0.0 or time.time() - last_note > max(interval, 15):
            last_note = time.time()
            out(line + "\n")
        time.sleep(0.25 if snap is None else interval)
        snap = snapshot()
    if tty and not ndjson:
        out("\r\033[2K")

    if snap is None:
        # Generic on purpose: this function has no verb of its own, only a
        # status filename ("pkg-status.json", "build-status.json", ...).
        # `start_hint` is how the caller -- the only one who knows the exact
        # command -- gets that command into the message instead of a guess
        # derived from the filename, which could easily be wrong.
        verb = status_name.split("-status", 1)[0] or "run"
        raise Bail(f"no {verb} run has published a status here", EX_FAIL,
                   start_hint or f"start one, then `porthole {verb} watch` "
                                 f"finds it")

    # The grace window ran out with nothing new. Say so plainly, then fall
    # through and render the stale run -- clearly labelled as the PREVIOUS
    # run, not left to look current the way the silent version did.
    if is_stale(snap):
        if not ndjson:
            # Coloured inline (not via `ctx.out.paint`) because this module
            # has no `ctx` and should not grow one just to colour a warning:
            # this line exists to stop the reader mistaking a stale run for
            # theirs, which is the whole subject of this branch, so the
            # colour is signal, not decoration.
            note = "  no new build started -- showing the previous run:"
            out((f"\033[33m{note}\033[0m" if tty else note) + "\n")
    else:
        while True:
            live = liveness(snap)
            if ndjson:
                out(json.dumps(snap) + "\n")
            elif tty:
                out("\r\033[2K  " + line_of(snap))
            elif time.time() - last_note > max(interval, 15):
                last_note = time.time()
                out("  " + line_of(snap) + "\n")
            if live != "running":
                break
            time.sleep(interval)
            snap = snapshot() or snap
        if tty and not ndjson:
            out("\r\033[2K")

    if not ndjson:
        head, rows = status_report(snap)
        out("  " + head + "\n")
        for label, value in rows:
            out(f"  {label:<10}  {value}\n")
    return EX_OK if liveness(snap) == "done" else EX_FAIL
