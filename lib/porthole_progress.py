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
import contextlib
import json
import os
import pathlib
import re
import shutil
import sys
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


# GN says the same things in a different vocabulary. cmake and meson write a
# sentence -- "Building CXX object foo.o" -- and every pattern above looks for
# its verb. GN writes a terse rule name in that position instead, and none of
# them match, so EVERY step of a chromium build classified as `other`, no rate
# sample was ever recorded, and `rate` and `eta` stayed None for the whole
# five hours. Measured on chromium 152.0.7977.82 for aarch64, 56135 steps:
# CXX 2809, ACTION 2658, CC 1355, COPY 334, ASM 125, AR 82, SOLINK 5, LINK 4.
#
# ACTION and COPY are deliberately NOT compiles, for exactly the reason
# `Generating` is not: an ACTION is one python or node process holding one
# core while the rest sit idle, and counting those toward a rate is what
# produced the 74-hour ETA this module already exists to avoid. They are 51%
# of this build's steps, so the distinction is not academic.
_GN_VERB = re.compile(r"\[\d+/\d+\]\s+([A-Z][A-Z0-9_]+)\b")
_GN_KIND = {
    "CC": "compile", "CXX": "compile", "ASM": "compile",
    "OBJC": "compile", "OBJCXX": "compile", "SWIFT": "compile",
    "AR": "link", "LINK": "link", "SOLINK": "link",
    "SOLINK_MODULE": "link",
    "ACTION": "generate", "COPY": "generate", "STAMP": "generate",
}


def step_kind(line: str) -> str:
    """`compile` | `link` | `generate` | `other` for one ninja step line."""
    line = _STAMP.sub("", line.lstrip())
    for name, pattern in _STEP_KIND:
        if pattern.search(line):
            return name
    verb = _GN_VERB.search(line)
    if verb:
        kind = _GN_KIND.get(verb.group(1))
        if kind:
            return kind
        # RUST_BIN, RUST_RLIB, RUST_CDYLIB, RUST_MACRO -- all of them compile
        # a crate, and naming each one is a list that goes stale.
        if verb.group(1).startswith("RUST"):
            return "compile"
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


# ph-build.sh's own convention, and it has been consistent since the file was
# written: `>> ` announces a STEP, `>>   ` (indented) is detail or advice about
# it. 63 lines follow it. Phase detection ignored the distinction and read
# every `>>` line as an announcement, so prose flipped the phase:
#
#   >>   this rung flashes the aport apk, so the tree cannot affect it.
#   >>   device. boot.img above is complete and flashable:
#
# both set `phase: flash` on rungs that had not reached, and in the second case
# would never reach, anything of the sort. Reported as `porthole build status`
# saying `phase flash` about a finished `image` run.
_STEP_LINE = re.compile(r"^\s*>>\s(?=\S)")
_ADVISORY = re.compile(r"\b(?:NOTE|WARNING|FATAL|REFUSING)\b")
# A command inside quotes or backticks is being TALKED ABOUT, not run. Both
# porthole and pmbootstrap quote commands in their advice, and the phase
# detector read every one of them as the step itself:
#
#   >>   and `pmbootstrap export` packs boot.img from it.
#   NOTE: To export the rootfs image, run 'pmbootstrap install' first
#   >>   pmbootstrap installed here, with its chroots):
#
# The last is not even quoted -- it is the word "installed" in a sentence --
# which is why the `>>` half of this rule keys on the indent convention rather
# than on punctuation.
_QUOTED = re.compile(r"""['"`]\s*pmbootstrap""")


def is_step_line(line: str) -> bool:
    """Does this line ANNOUNCE a step, as opposed to commenting on one? PURE.

    ph-build.sh's own convention, consistent across its 63 detail lines:
    `>> ` announces, `>>   ` (indented) explains.
    """
    line = line or ""
    return bool(_STEP_LINE.match(line)) and not _ADVISORY.search(line)


def names_a_running_command(line: str) -> bool:
    """Is `pmbootstrap <sub>` on this line the command being run? PURE.

    False for prose about it -- quoted, or in a sentence that is advice.
    """
    line = line or ""
    return not _QUOTED.search(line) and not _ADVISORY.search(line)


def phase_of(line: str, current: str) -> str:
    """Which phase a line says we are in, or `current` if it says nothing.

    Order matters: `>> verifying the exported image` mentions both export and
    verify, and the later phase is the true one.
    """
    for name, pattern in _MARKERS:
        # A `>>` pattern only counts on a line that announces a step. The two
        # patterns that also match pmbootstrap's own output (`pmbootstrap
        # export`, `pmbootstrap install`) are left alone -- they are the
        # command, not prose about it.
        if not pattern.search(line):
            continue
        # A `>>` marker counts only on a line that announces a step; the bare
        # `pmbootstrap export|install` alternative counts only when the
        # command is being run rather than quoted in advice. Without both,
        # every sentence that mentions a step set the phase to it -- which is
        # how a finished `image` run reported `phase flash`.
        if is_step_line(line):
            return name
        if ">>" not in line and names_a_running_command(line) and re.search(
                r"pmbootstrap (?:export|install)", line):
            return name
        continue
    if _COMPILE.match(line) and current == "":
        return "make"
    return current or ("make" if _COMPILE.match(line) else current)


def is_compile_line(line: str) -> bool:
    return bool(_COMPILE.match(line))


# Which phases each rung actually runs, in order. A fact about
# tools/ph-build.sh's own functions -- not about a device -- so it belongs in
# a table here rather than in a profile, exactly like ACTIONS.
#
# This exists to answer "how far along" when NOTHING else can. fraction()
# returns None the moment a run overruns its baseline, and it is right to:
# clamping to 0.99 there reported "99%, eta 7s" for the fourteen remaining
# minutes of a 14m42s build. But the fallback was no answer at all, for the
# whole rest of the run -- which on a full compile is most of it. Reported
# from a real session as `[ unknown ] -- package 1m42s eta --`, then the same
# bar at 4m38s and again at 6m09s: three screenshots of one defect.
#
# A phase position is not a percentage and is not presented as one -- it
# renders as `3/5`. It cannot be fooled by a run being bigger than the last,
# because it measures where the run IS rather than how much is left.
RUNG_PHASES = {
    "mod":     ("make", "push"),
    "boot":    ("make", "verify", "flash"),
    "fast":    ("make", "package", "install", "export", "flash"),
    "kernel":  ("make", "package", "install", "export", "verify", "flash"),
    "upgrade": ("make", "package", "install", "export", "verify", "flash"),
}


def phase_position(rung, phase):
    """(n, total) -- where a run is in its rung's phase sequence, or None.

    None for a rung with no table and for a phase it does not list, because a
    made-up position is exactly the fabricated confidence this whole module
    refuses elsewhere.
    """
    phases = RUNG_PHASES.get((rung or "").split("|")[0])
    if not phases or phase not in phases:
        return None
    return phases.index(phase) + 1, len(phases)


# What the terminal on the other end can actually render. Same two questions
# `porthole_cli.Out` asks -- colour only on a tty with NO_COLOR unset, unicode
# only when the encoding can carry it -- asked here too because this module
# renders without a `ctx`, and a bring-up host is often a minimal container
# where a block character is a UnicodeEncodeError rather than cosmetics.
Style = collections.namedtuple("Style", "colour unicode")
PLAIN = Style(colour=False, unicode=False)


def detect_style(stream=None) -> Style:
    stream = sys.stdout if stream is None else stream
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError):
        tty = False
    enc = (getattr(stream, "encoding", None) or "ascii").lower()
    return Style(colour=bool(tty) and not os.environ.get("NO_COLOR")
                 and os.environ.get("TERM") != "dumb",
                 unicode="utf" in enc)


_ANSI = re.compile(r"\033\[[0-9;]*m")


def visible_len(text: str) -> int:
    """Width on screen: an escape sequence occupies no columns.

    `clip` and the stall-note budget both measure with this. Measuring a
    coloured string with `len` overstates it by the length of its escapes,
    which cuts the line short of the terminal -- and, worse, can cut INSIDE a
    sequence and leave the rest of the screen dyed.
    """
    return len(_ANSI.sub("", text))


def tint(text: str, colour: str, style) -> str:
    """Colour, if this terminal has any. Never widens the visible text."""
    codes = {"red": "31", "green": "32", "yellow": "33", "cyan": "36",
             "grey": "90", "bold": "1"}
    code = codes.get(colour)
    return f"\033[{code}m{text}\033[0m" if (style.colour and code) else text


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


# The eighth-blocks, in order. `""` first: a leading edge less than an eighth
# of a cell wide is no edge at all, not a whole one.
_EIGHTHS = ("", "\u258f", "\u258e", "\u258d", "\u258c", "\u258b", "\u258a",
            "\u2589")


def bar(fraction, width: int = 18, style=None) -> str:
    """A bar, or the word `unknown` when there is no fraction to draw.

    NOT `[??????????????????]`, which is what this drew for every kernel rung
    without history -- the common case. Eighteen question marks read as a
    broken terminal, and reported as one: "it's not good looking this state,
    an user may think it's stale". The word says exactly the same thing --
    nothing was measured -- without the alarm, and an empty bar was never an
    option because empty reads as 0%, which is a measurement nobody made.
    Same width either way, so the columns after it do not move.

    ASCII stays exactly as it was -- `[====>   ]` is what a log, a pipe and
    every existing test see. The block form is for a terminal that asked for
    it, and it is the same measurement drawn at eight times the resolution.
    """
    style = PLAIN if style is None else style
    if fraction is None:
        return "[" + "unknown".center(width)[:width] + "]"
    fraction = max(0.0, min(1.0, fraction))
    if not style.unicode:
        filled = int(fraction * width)
        return "[" + "=" * filled + ">" * (1 if filled < width else 0) + \
               " " * (width - filled - (1 if filled < width else 0)) + "]"
    # Eighths, so the leading edge moves eight times per cell instead of
    # once. On a nine-thousand-step build a whole cell is 500 steps: a bar
    # that only moves in cells sits still for ten minutes at a time, which is
    # indistinguishable from the frozen one this whole change is about.
    cells = fraction * width
    full = int(cells)
    edge = _EIGHTHS[int((cells - full) * 8)] if full < width else ""
    body = "\u2588" * full + edge
    return "[" + body + "\u2500" * (width - visible_len(body)) + "]"


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

    And when a rung has a compile baseline, the elapsed fallback is NOT used to
    fill in its quiet prologue. It reads the same way -- a bar climbing from 0%
    -- but it is a different measurement, and the handover between the two ran
    backwards: `fast` sat at 50% through pmbootstrap's setup, then the first
    `CC` line arrived and the compile count, which is the honest one, put it at
    0%. A bar that resets is read as a build that restarted. Unknown until the
    thing being counted starts is the same answer this function gives everywhere
    else it cannot see.
    """
    runs = (history or {}).get(rung) or {}
    lines = runs.get("compile_lines")
    if isinstance(lines, int) and lines > 0:
        if compile_seen <= 0:
            return None
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


def history_key(rung: str, rebuilding: bool) -> str:
    """The bucket a run's timing is remembered under.

    A rung's cost can be BIMODAL and the predictor was unimodal. `fast` is
    install + export + flash when the target apk already exists, and a full
    199-patch compile plus module strip plus compression when it does not --
    measured at 6m43s against 21m24s. One bucket learns the mean of both and
    is wrong by 3.5x whenever the package changed.

    Legacy unkeyed entries stop matching, which is deliberate: the stored
    403.4 IS that mean, so seeding either bucket with it reintroduces the
    error in the bucket nobody would look in. No history is honest, and
    `eta unknown` on a first run is already what this module does.
    """
    return "{}|{}".format(rung, "rebuild" if rebuilding else "cached")


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

    def __init__(self, rundir, rung: str, key: str = ""):
        self.rundir = pathlib.Path(rundir)
        self.rung = rung
        # The history bucket, which may differ from the rung -- see
        # `history_key`. Defaults to the rung itself, so every existing
        # caller (one bucket per rung) keeps behaving exactly as before.
        self.key = key or rung
        self.history = load_history(rundir)
        self.started = time.time()
        self.phase = ""
        self.compile_seen = 0
        self.last = ""
        self.last_at = self.started
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
        self.last_at = time.time()

    def _fraction(self):
        """Where this tracker's percentage comes from.

        A seam, not indirection for its own sake: a package build has a real
        denominator from ninja and a kernel build does not, and that is the
        ONLY thing that differs between them. Overriding one method keeps the
        publish/history/log path a single implementation.
        """
        return fraction(self.history, self.key, self.elapsed, self.compile_seen)

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
        total = (self.history or {}).get(self.key, {}).get("compile_lines")
        if recent and isinstance(total, int) and total > self.compile_seen:
            return (total - self.compile_seen) / recent
        return eta(self.history, self.key, self.elapsed, frac)

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
                "last_at": round(self.last_at, 1),
                "last_age": round(time.time() - self.last_at, 1),
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
            record(self.rundir, self.key, self.elapsed, self.compile_seen)

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


_STEPS = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


def steps_of(snap):
    """`(done, total)` from a snapshot's `steps` field, else `(None, None)`."""
    match = _STEPS.match(str((snap or {}).get("steps") or ""))
    if not match:
        return (None, None)
    done, total = int(match.group(1)), int(match.group(2))
    return (done, total) if total > 0 and done <= total else (None, None)


def observed_rate(snap, samples, now: float):
    """A snapshot with `rate` and `eta` filled in from the counter MOVING.

    The tracker owns those fields and this never overwrites them. It exists
    for the case where it published neither and the counter in front of the
    reader is visibly advancing anyway:

      * a build already running when porthole was upgraded -- its tracker
        holds the modules it imported at start, so a classifier fix cannot
        reach it and every step stays `other` until the process exits;
      * any tracker whose step kinds this version does not recognise.

    Both left `rate --  eta --` beside a percentage that was climbing, which
    reads as a refusal to estimate rather than as a gap.

    The unit here is NINJA STEPS per second, which is what the counter beside
    it counts -- not the tracker's compiles per second. A watcher cannot see
    step kinds, only the number, and inventing a compile share it has no way
    to measure would be a guess wearing the tracker's units.

    `window_rate` still owns the refusal: too short a span or too few steps
    and this returns the snapshot untouched, so a build parked inside one
    generator step reports `--` rather than a number nobody should act on.
    """
    if not snap or (snap.get("state") or "running") != "running":
        return snap
    if snap.get("rate") is not None:
        return snap
    done, total = steps_of(snap)
    if done is None:
        return snap
    sample = (now, done)
    if not samples or samples[-1][1] != done:
        samples.append(sample)
    rate = window_rate(samples, now)
    if not rate:
        return snap
    out = dict(snap)
    out["rate"] = round(rate, 2)
    if out.get("eta") is None and total > done:
        # TWO ESTIMATORS, ON PURPOSE, because they answer different questions.
        # `rate` is the trailing window: "how fast is it going right now",
        # which is what somebody watching wants to see. An ETA over six hours
        # is a different question, and the window is a bad answer to it -- it
        # predicts the next three minutes. Measured on a chromium build parked
        # in a generator phase at a quarter of its own average speed, the
        # windowed ETA read 11h30m and climbed to 13h17m over two minutes,
        # while the run had about six hours left. An ETA that grows while you
        # watch it is what teaches people to ignore the field.
        #
        # The remainder will contain the same mix of fast and slow phases the
        # run has already been through, so the rate ACROSS THE WHOLE RUN is
        # the better predictor of it. It reads slightly slow -- `started`
        # includes fetch and prepare, which produce no steps -- and that errs
        # long, which is the safe direction.
        started = snap.get("started")
        span = None if not started else now - started
        sustained = (done / span) if span and span > 0 else None
        out["eta"] = (total - done) / (sustained or rate)
    return out


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

    def __init__(self, rundir, rung: str, key: str = ""):
        super().__init__(rundir, rung, key=key)
        self.ninja_total = 0
        self.step = ""
        self.compiles = 0
        # ninja's step number when this tracker saw its first compile. The
        # compile share has to be measured over what was OBSERVED: a tracker
        # that attached at step 2600, or resumed one, has a `compiles` count
        # that describes its own window and a `compile_seen` that describes
        # the whole build, and dividing those two mixes them.
        self._first_step = None

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
                if self._first_step is None:
                    self._first_step = self.compile_seen
                self._samples.append((time.time(), self.compiles))
        self.last = line[:200]
        self.last_at = time.time()

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
        # ...converted into the unit `best` is measured in. The rate counts
        # COMPILE steps per second while `remaining` counts ninja steps of
        # every kind, and dividing one by the other overstates the ETA by
        # however much of the build is not compiling -- 1.9x on chromium,
        # where ACTION and COPY are 51% of the steps.
        #
        # The share is measured over the steps this tracker actually watched,
        # which is not the same as the whole build: one that attached at step
        # 2600 has seen every compile since then and none before, and
        # compiles/compile_seen would read that as a 13% compile build.
        if self._first_step is not None:
            watched = self.compile_seen - self._first_step
            if watched > 0 and self.compiles > 0:
                remaining *= min(1.0, self.compiles / float(watched))
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
        return fraction(self.history, self.key, self.elapsed, 0)


_LINE_WIDTH = 100  # matches status_report's own `last`-truncation budget


def last_age_of(snap, now=None):
    """How long since the run last said anything, or None.

    From `last_at` -- an absolute stamp -- in preference to `last_age`, which
    is a number frozen at the instant the writer published it. The difference
    is the whole point of the field to a reader: `last_age` stops growing the
    moment the writer stops, so a wedged or killed build shows the same age
    forever, which is exactly the "is this thing stale?" doubt this is meant
    to settle. Derived from a stamp, the number keeps climbing whatever the
    writer is doing, and a reader can tell quiet from dead by watching it.
    """
    at = (snap or {}).get("last_at")
    if isinstance(at, (int, float)):
        return max(0.0, (time.time() if now is None else now) - at)
    age = (snap or {}).get("last_age")
    return age if isinstance(age, (int, float)) else None


def activity_of(snap, now=None) -> str:
    """What the run is DOING, with the age of that fact. Pure.

    The reported defect: `[??????] -- push 4m16s eta --`, unchanged for
    minutes, while the status file held `>> reboot 1/4: burning a boot retry`.
    The information was already recorded and the watcher simply never drew it,
    so a coarse phase word was all the reader got. This is that line.

    The age leads because it is the field that MOVES. A render that looks
    identical whether the build is working or dead is the complaint; a number
    that ticks every second is the answer to it.
    """
    age = fmt_dur(last_age_of(snap, now))
    last = ((snap or {}).get("last") or "").strip()
    if not last:
        return "{:>7} ago  (nothing said yet)".format(age)
    return "{:>7} ago  {}".format(age, last)


def clip(text: str, width) -> str:
    """Cut to the terminal's width. `watch` repaints in place, and a line that
    wraps leaves its overflow behind on the next redraw -- the smear reads as
    a corrupted display, which is worse than the truncation it came from."""
    if not width or visible_len(text) <= width:
        return text
    # Cutting a coloured string by index can land inside an escape sequence
    # and dye the rest of the screen, so the colour comes off and the plain
    # text is cut. It used to return such a line UNCUT instead, which is the
    # smear this function exists to prevent, dressed as caution: the activity
    # row carries the child's own output, pmbootstrap colours every line it
    # prints, and a 170-column `(native) install ...` line came back 192
    # columns wide. The row wrapped, the block painter walked back up over the
    # number of ROWS it drew rather than the screen lines they took, and every
    # repaint stranded the header above it -- one `fast ... running` per long
    # line, for the length of the build.
    return _ANSI.sub("", text)[:max(0, width - 1)] + "\u2026"


def term_width(default: int = 100) -> int:
    """Columns available for a repainted line, minus one.

    The last column is left alone deliberately: writing into it makes most
    terminals wrap immediately, which is the smear `clip` exists to avoid.
    """
    return max(40, shutil.get_terminal_size((default, 24)).columns - 1)


def line_of(snap, width: int = 18, now=None,
            budget: int = _LINE_WIDTH, style=None) -> str:
    """The one-line view, from a snapshot rather than from a live tracker.

    Module-level so that something following the status FILE renders exactly
    what the build's own terminal renders. Two implementations of this line is
    how a watcher ends up disagreeing with the thing it is watching.
    """
    frac = snap.get("progress")
    # A ninja step that is Generating rather than Building is shown as such.
    # It is the single most useful thing on this line during a stall: the
    # counter is not moving, and "generating" says that is expected while
    # "build" invites the reader to conclude the thing has hung.
    phase = snap.get("phase") or "starting"
    phase = {"generate": "generating", "link": "linking"}.get(
        snap.get("step"), phase) if phase == "build" else phase

    # A FINISHED RUN IS NOT STILL IN ITS LAST PHASE. `fast done [ unknown ]
    # -- install  6m09s eta --` sat on screen after the phone had been
    # flashed AND rebooted, and an agent went on polling a build that had
    # been over for minutes -- which is the cost of a label that outlives
    # what it describes. The state is in the snapshot; use it.
    state = (snap or {}).get("state") or "running"
    if state == "done":
        frac, phase = 1.0, "done"
    elif state in ("failed", "stale"):
        phase = state

    pct = "--" if frac is None else "{:>3d}%".format(int(frac * 100))
    # No fraction: say where the run IS instead of saying nothing. Rendered as
    # `3/5`, never as a percentage, because it is a position and not a
    # measurement of work remaining.
    if frac is None:
        spot = phase_position(snap.get("rung"), phase)
        if spot:
            frac = spot[0] / float(spot[1])
            pct = "{}/{}".format(*spot)
    # A finished run has no ETA. `fast done [ unknown ] eta 6s` is the same
    # class of lie as the phase that outlived its run, one field over: a
    # forward-looking claim about something that already happened.
    eta = snap.get("eta") if state == "running" else None
    rate = snap.get("rate")
    # Rate is shown for package builds and omitted for kernel rungs, which
    # have no step count to have a rate over.
    tail = "" if "rate" not in snap else \
        " {:>6}".format("--/s" if not rate else "{:.1f}/s".format(rate))
    base = "{} {} {:<10}{} {:>7} eta {:>7}".format(
        bar(frac, width, style), pct, phase, tail,
        fmt_dur(snap.get("elapsed")), fmt_dur(eta))
    # `stall_note()` had exactly one caller -- `status_report`, reached only
    # after a run has already stopped -- so a `watch`ed build never showed
    # it: `watch` renders THIS function while the run is still live. Without
    # it, a healthy `pmbootstrap install` silent for minutes looked identical
    # to a hang. Appended here, not on a second line: `watch` repaints in
    # place on a tty and a second line would corrupt the redraw.
    last = snap.get("last")
    # A run that has stopped cannot be stalled, and saying "no output for
    # 1m39s" about a finished build is noise wearing the costume of a
    # warning.
    if not last or state != "running":
        return base
    note = stall_note(last, last_age_of(snap, now) or 0.0, phase)
    if not note:
        return base
    sep = " · "
    room = budget - len(base) - len(sep)
    # 20 left room for `no output for 1m39s -- the…`, which is a fragment
    # rather than a sentence: it stops before the only clause that carries
    # the reassurance. Below what a useful cut needs, say nothing -- the bar
    # and the activity line are still there.
    if room < 36:
        return base
    if len(note) > room:
        # Cut on a word boundary, and SAY it was cut -- a sentence that stops
        # dead at "the process is" reads as a broken renderer, which is the
        # impression this whole line exists to remove.
        note = note[:room - 1].rsplit(" ", 1)[0] + "\u2026"
    return base + sep + note


# A spinner has one job: say that this display is alive and the work is
# moving. The first cut here indexed the frame by the build's own last line,
# so on a package that lands a step every ten seconds it sat perfectly still
# -- honest, and indistinguishable from a hung terminal, which is the wrong
# trade for the one glyph whose entire purpose is to move.
#
# So it turns on the clock, and STOPS when the silence stops being normal --
# `stall_note` already knows what normal is for each phase, and the mark it
# freezes into is the same one a failed run gets. A spinning spinner beside a
# climbing age means "working"; a frozen mark beside it means "not, and here
# is why", which is the distinction the reader actually needs.
_SPIN = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
_SPIN_ASCII = "|/-\\"
_MARKS = {"done": ("\u2714", "+"), "failed": ("\u2718", "x"),
          "stale": ("\u2049", "!")}
SPIN_FPS = 10.0


def spinner(snap, style=None, now=None) -> str:
    """The one-character state mark at the head of the activity row. Pure."""
    style = PLAIN if style is None else style
    snap = snap or {}
    state = snap.get("state") or "running"
    if state != "running":
        fancy, plain = _MARKS.get(state, ("\u00b7", "."))
        return fancy if style.unicode else plain
    now = time.time() if now is None else now
    age = last_age_of(snap, now) or 0.0
    if stall_note(snap.get("last") or "", age, snap.get("phase") or ""):
        return "\u2049" if style.unicode else "!"
    frames = _SPIN if style.unicode else _SPIN_ASCII
    return frames[int(now * SPIN_FPS) % len(frames)]


_STATE_COLOUR = {"running": "cyan", "done": "green", "failed": "red",
                 "stale": "yellow"}


def header_segments(snap, width):
    """`[(text, tone)]` for the header row: what is building, how far through
    it, and how it is going. Pure.

    SEGMENTS, not a formatted string, because the painter used to re-parse the
    line it had just built -- looking for the state at the end and a `/` in
    the last word -- and got it wrong the moment the step count arrived,
    printing `...webkit2gtk-6.0    8358358/9429running`. A renderer that has
    to reverse-engineer its own output is a bug waiting for its next field.

    Drops the step count before the state, and the state before the name, as
    the terminal narrows: the name is the one thing on this row that cannot
    be inferred from anything else on screen.
    """
    snap = snap or {}
    name = str(snap.get("rung") or "?")
    state = snap.get("state") or "running"
    steps = str(snap.get("steps") or "")
    for want_steps, want_state in ((steps, state), ("", state), ("", "")):
        right = [(part, tone) for part, tone
                 in ((want_steps, "steps"), (want_state, "state")) if part]
        wide = sum(len(part) for part, _ in right) + 3 * (len(right) - 1)
        room = width - 2 - len(name) - max(0, wide) - 2
        if room < 2:
            continue
        out = [("  ", "pad")]
        prefix, sep, target = name.rpartition(":")
        if sep:
            out.append((prefix + sep, "dim"))
        out.append((target, "name"))
        out.append((" " * (room + 2), "pad"))
        for index, (part, tone) in enumerate(right):
            if index:
                out.append(("   ", "pad"))
            out.append((part, tone))
        return out
    return [("  ", "pad"), (clip(name, max(0, width - 2)), "name")]


def header_of(snap, width) -> str:
    """The header row as plain text."""
    return "".join(text for text, _ in header_segments(snap, width))


def watch_lines(snap, width=None, now=None, style=None, footer=""):
    """The block `watch` paints: what is building, how far, what it is doing,
    and -- last and quietest -- how to leave.

    A FIXED number of rows, so an in-place repaint can walk back up a known
    number of them and the block never leaves a stale row on screen. Three,
    or four when there is a footer; a caller either passes one every repaint
    or never.

    EMPHASIS IS A BUDGET. The eye should land on the percentage, the ETA and
    the name, in that order -- those are the three things somebody opens this
    to read. Everything else is context: the rate, the elapsed, the labels and
    the whole footer are dimmed, and the chrome that used to sit at the TOP in
    full brightness (the exit hint, the reattach explanation) now sits at the
    bottom in grey, where it can be read once and then ignored.

    Colour is applied AFTER clipping, and only to segments whose plain width
    is already known: `clip` measures with `len`, and a line cut in the middle
    of an escape sequence dyes the rest of the terminal.
    """
    width = term_width() if width is None else width
    style = detect_style() if style is None else style
    # `liveness` answers by asking about a pid, so it can only be consulted
    # when there IS one. A log-derived snapshot has none -- nothing published
    # it -- and running it through `liveness` calls a build we are watching
    # advance `stale`, which is both wrong and the exact word this display
    # exists to stop misapplying.
    state = (snap or {}).get("state") or "running"
    if state == "running" and (snap or {}).get("pid") is not None:
        state = liveness(snap)
    colour = _STATE_COLOUR.get(state, "cyan")

    head = clip(header_of(snap, width), width)
    # `budget` is the note's room on the summary line, and it is the caller's
    # width rather than the hardcoded 100 the build's own painter uses: a
    # terminal wider than that was cutting the explanation mid-sentence.
    body = clip("  " + line_of(snap, now=now, budget=max(40, width - 2),
                               style=style), width)
    mark = spinner(snap, style, now)
    tail = clip("  {}  {}".format(mark, activity_of(snap, now)), width)
    rows = [head, body, tail]
    if footer:
        rows.append(clip("  " + footer, width))

    if style.colour:
        rows[0] = _paint_header(snap, width, colour, style)
        rows[1] = _paint_body(body, colour, style)
        cut = tail.find(mark)
        if cut >= 0:
            # The age is context and is dimmed; the line the build actually
            # printed is NOT -- it was rendering in the same grey as the
            # footer, so the two most different things on screen (what the
            # build is doing, and how to quit) looked identical.
            rest = tail[cut + len(mark):]
            ago = rest.find("ago")
            if ago >= 0:
                rest = tint(rest[:ago + 3], "grey", style) + rest[ago + 3:]
            rows[2] = tail[:cut] + tint(mark, colour, style) + rest
        if footer:
            rows[3] = tint(rows[3], "grey", style)
    return rows


def block_painter(out):
    """`(paint, clear)` for repainting a fixed-height block in place.

    `watch` has carried this arithmetic inline since it existed; the build's
    own painter carried a one-line version of it and so drew a one-line bar,
    while `porthole pkg watch` drew the four-row block from the same tracker.
    Same numbers, two different pictures, and the build -- the thing somebody
    actually sits and watches -- had the poorer one.

    `paint` walks the cursor back up over the rows it painted last time, so
    the caller must not print anything else between calls. In the build's case
    nothing does: the raw stream goes to stdout only under --verbose, which
    turns the bar off precisely because the two would fight.
    """
    painted = [0]

    def paint(lines):
        up = "\033[{}A".format(painted[0] - 1) if painted[0] > 1 else ""
        painted[0] = len(lines)
        out(up + "\n".join("\r\033[2K" + text for text in lines))

    def clear():
        if painted[0]:
            out("\r\033[2K" + "\033[1A\r\033[2K" * max(0, painted[0] - 1))
            painted[0] = 0

    return paint, clear


def _paint_header(snap, width, colour, style) -> str:
    """Tint per segment. Emphasis budget: the name and the step count are what
    the reader came for, the namespace is context, the state carries the one
    colour that means something."""
    tones = {"name": "bold", "steps": "bold", "dim": "grey", "state": colour}
    return "".join(tint(text, tones[tone], style) if tone in tones else text
                   for text, tone in header_segments(snap, width))


def _paint_body(body: str, colour: str, style) -> str:
    """The bar in the state's colour, the percentage and the ETA bold, and
    every label and secondary number grey.

    Segment boundaries come from `line_of`'s own fixed format -- `[bar] pct
    phase rate elapsed eta VALUE` -- so this reads positions rather than
    guessing them, and a change to that format shows up as a test failure
    here rather than as a smear on somebody's terminal.
    """
    close = body.find("]")
    if close < 0:
        return body
    indent = body[:len(body) - len(body.lstrip())]
    bar_part = indent + tint(body[len(indent):close + 1], colour, style)
    rest = body[close + 1:]
    # `{:>4}%` -- the percentage is the first token after the bar.
    pct_end = rest.find("%")
    if pct_end < 0:
        return bar_part + rest
    pct = tint(rest[:pct_end + 1], "bold", style)
    rest = rest[pct_end + 1:]
    # The ETA's value is everything after the last ` eta `; its label, and
    # everything between the phase and it, is context.
    label = rest.rfind(" eta ")
    if label < 0:
        return bar_part + pct + tint(rest, "grey", style)
    middle = tint(rest[:label + 5], "grey", style)
    value, _, note = rest[label + 5:].partition(" \u00b7 ")
    tail = tint(value, "bold", style)
    if note:
        tail += tint(" \u00b7 " + note, "grey", style)
    return bar_part + pct + middle + tail


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
    now = round(time.time(), 1)
    snap = {"rung": rung, "phase": "", "state": "running", "pid": pid,
            "elapsed": 0.0, "progress": None, "eta": None,
            "compile_lines": 0, "last": "", "last_at": now, "last_age": 0.0,
            "started": now}
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


# How long a build may say nothing before the display owes the reader a
# reason. Packaging a kernel is legitimately silent for minutes; a reader who
# is not told that concludes it has hung, and kills a healthy build.
STALL_AFTER_S = 90.0

# What a long silence MEANS, keyed on the last thing that was said. Same shape
# as _DIAGNOSES in porthole_cmd_build: a lookup table, because the alternative
# is every porter rediscovering the same three answers, and the last line is
# the one thing they all have in hand.
_STALL_NOTES = (
    (re.compile(r"\bDONE!|\bBuilding package\b|\bfakeroot\b|\bcompress",
                re.I),
     "no progress signal during packaging -- abuild is compressing the kernel "
     "and its modules, which is normally several minutes and prints nothing"),
    (re.compile(r"\bmodules_install\b|\bstrip\b|\bMODPOST\b", re.I),
     "no progress signal while modules are installed and stripped; this step "
     "is quiet and long on a kernel with many modules"),
    (re.compile(r"\bapk add\b|\binstalling\b|\bdependenc", re.I),
     "no progress signal while build dependencies install"),
)


# How long each phase may legitimately say nothing before the display owes
# the reader a reason. `pmbootstrap` relays its real output to log.txt rather
# than stdout, and packaging a kernel -- strip, then compress every module --
# is quiet for minutes on purpose. A flat 90 s bound reported "no output for
# 1m39s -- the process is still running" against a build that was compiling
# perfectly happily, which teaches the reader to distrust the one line that
# exists to stop them killing a healthy build.
_PHASE_PATIENCE = {"package": 420.0, "install": 300.0, "export": 300.0}


def stall_note(last: str, silence: float, phase: str = "") -> str:
    """Why nothing has been said for a while, or "" if it is too soon to ask.

    Never renders a bar as [??????] with `eta --` and no explanation. The
    report is explicit that this one line removes the entire "why is this
    taking so long" anxiety, and the anxiety is what makes people kill builds
    that were working.
    """
    if silence < _PHASE_PATIENCE.get(phase, STALL_AFTER_S):
        return ""
    for pattern, note in _STALL_NOTES:
        if pattern.search(last or ""):
            return note
    # ponytail: no CPU sampling, so "quiet and working" and "quiet and wedged"
    # still read the same here. Upgrade path: read the container's cgroup
    # cpu.stat (or the child's descendants for a host build) on the heartbeat
    # and say which it is. Deferred because following pmbootstrap's log.txt
    # shrank the silent window to the compression tail.
    return "no output for {} -- the process is still running".format(
        fmt_dur(silence))


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
        # `last_at` when there is no started/elapsed to add up: a snapshot
        # re-read from a log has no idea when the build BEGAN, and reporting
        # `FAILED` with no when at all is the half-answer this row exists to
        # replace.
        stopped = finished_at(snap)
        if stopped is None and isinstance(snap.get("last_at"), (int, float)):
            stopped = snap["last_at"]
        if stopped is not None:
            head += "  {} ago".format(fmt_dur(now - stopped))
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
        age = last_age_of(snap, now)
        label = str(snap["last"])[:100]
        if age is not None and age >= STALL_AFTER_S:
            label = "({} ago) {}".format(fmt_dur(age), label)
        rows.append(("last", label))
        note = stall_note(snap.get("last", ""), age or 0.0)
        if note and live == "running":
            rows.append(("why", note))
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


# Every ndjson object's key set -- `snapshot()`'s own keys (Tracker's and
# PkgTracker's on-disk shape, and `publish_pending`'s) plus `note`, which a
# snapshot on disk never carries but every EMITTED object must, live or
# waiting, or `obj["note"]` KeyErrors on exactly the objects that have
# nothing to say. Used as the "waiting, and nothing has EVER published here"
# fallback -- all `None`, `note` included -- so the emitted object's key set
# never depends on which branch, or how much history, produced it.
NDJSON_KEYS = ("rung", "phase", "state", "pid", "elapsed", "progress", "eta",
              "compile_lines", "last", "last_at", "last_age", "started",
              "note")


# ---------------------------------------------------------- reattaching --
#
# The status file was never the source of truth, and treating it as one is
# what let a live build read as a finished one. pmbootstrap's own log.txt is
# the truth: the container writes it into a host-visible mount, it is
# append-only, every line carries a clock, and the `[n/N]` lines in it are
# the very same ones PkgTracker counts. A tracker that dies -- an agent's
# command timeout, a closed session, an outage -- destroys no progress at
# all. It only stops somebody READING a file that is still being written.
#
# So a watcher that finds nothing live re-derives a snapshot from that file
# instead of giving up. Same shape as the tracker's own, so every renderer
# below works on it unchanged.

# How much of the tail to read. log.txt is shared across every build the
# workspace has ever run -- 11 MiB here -- and only the end describes now.
LOG_TAIL_BYTES = 65536

# log.txt carries NO clock: pmbootstrap stamps the lines it relays to its
# stdout, not the ones it writes here (checked against 11 MiB of a real
# webkit log -- zero stamped lines). So the rate cannot be read out of the
# file; it is MEASURED, by sampling the step count across two polls of it.
# That is what the live tracker does too, which is the point: one way of
# getting a rate, whoever is doing the counting.


def log_steps(text: str):
    """`(done, total)` from the last `[n/N]` in a log tail, else `(None, None)`.

    From the END backwards: the tail holds thousands of step lines and only
    the last one is now.
    """
    for line in reversed(text.splitlines()):
        step = ninja_progress(line)
        if step:
            return step
    return None, None


# HOW A BUILD ENDS, IN ITS OWN WORDS.
#
# log.txt is the WORKSPACE's log, not a build's: every pmbootstrap invocation
# appends to it, and a two-second `chroot -- ls` refreshes its mtime exactly
# as a four-hour compile does. Treating that mtime as "the build I am
# following is still running" is what pinned a status line to `webkit2gtk-6.0
# 99% 9428/9429 . reattached` for ten hours after the build had already
# FAILED, and what kept `pkg watch` from ever reaching its verdict: the probe
# that ends a reattached watch was gated behind the log going quiet, and on a
# workspace anybody is using it never does.
#
# The log had the answer the whole time, three lines below the last step:
#
#     [9428/9429] Generating WebKitWebProcessExtension-6.0.typelib
#     ninja: subcommand failed
#     >>> ERROR: webkit2gtk-6.0: build failed
#
# Both markers NAME the package, which is what makes one of them this build's
# ending rather than a neighbour's in a log every build shares.
_BUILT = re.compile(r"^>>> (?P<name>\S+?)\*?: Create \S+\.apk\b")
_FAILED = re.compile(r"^>>> ERROR: (?P<name>\S+): build failed\b")

# pmbootstrap stamps its OWN lines `(pid) [HH:MM:SS]`. The build output it
# relays carries no clock at all, so this is the only time-of-day in the file
# -- and abuild's ending is always followed by one, because pmbootstrap says
# what it made of it.
_LOG_STAMP = re.compile(r"^\((\d+)\) \[(\d\d):(\d\d):(\d\d)\]")


def log_outcome(text: str, name: str, now=None):
    """`(state, when, line)` for how `name`'s build ended, else three Nones.

    Only what was written AFTER the last `[n/N]`: the tail of a shared log
    holds the endings of every build before this one, and the newest step
    line is the boundary between them. No step line means ninja never
    started here, and then nothing in this tail can be attributed -- the
    honest answer is that this cannot say.

    Pure, so the wording of abuild's two verdicts is a test rather than
    something rediscovered at the end of a four-hour build.
    """
    lines = text.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if ninja_progress(lines[index]):
            break
    else:
        return None, None, None
    for offset, line in enumerate(lines[index + 1:], index + 1):
        for pattern, state in ((_FAILED, "failed"), (_BUILT, "done")):
            match = pattern.match(line)
            if match and match.group("name") == name:
                return state, _stamped_at(lines[offset:], now), line.rstrip()
    return None, None, None


def _stamped_at(lines, now=None):
    """Wall clock for the first `(pid) [HH:MM:SS]` stamp in `lines`, or None.

    The file carries a time of day and no date, so the day has to come from
    somewhere: the most recent instant with that clock reading that is not in
    the future. A build that ended at 23:59 and is read at 08:44 the next
    morning is eight hours ago, and saying `0s ago` -- which is what the log's
    mtime says, because something unrelated touched it -- is the whole defect
    this exists to stop.
    """
    now = time.time() if now is None else now
    for line in lines:
        match = _LOG_STAMP.match(line)
        if not match:
            continue
        day = time.localtime(now)
        when = time.mktime((day.tm_year, day.tm_mon, day.tm_mday,
                            int(match.group(2)), int(match.group(3)),
                            int(match.group(4)), 0, 0, -1))
        return when - 86400.0 if when > now else when
    return None


def snapshot_from_log(text: str, name: str, mtime, now=None,
                      samples=None) -> dict:
    """A tracker-shaped snapshot re-derived from a log tail. Pure.

    Everything unknowable without the run that started the build is `None`
    and says so: there is no pid to ask about, and nothing here knows when
    the build began -- `elapsed` from a log tail would be a guess, and
    `fmt_dur(None)` already renders "--". The ETA is not a guess either: it
    is `previous`'s measured rate applied to the steps that remain, and
    absent on the first reading because one sample cannot have a speed.

    `samples` is the `(when, done)` deque the caller has been filling from
    this log -- the same shape and the same `window_rate` the live tracker
    uses, so a reattached watcher and the build's own bar cannot quote
    different speeds for one build. The clock in it is the LOG's mtime, not
    the watcher's: a log that has not been touched has not advanced, and
    dividing by the watcher's own elapsed time reports a slowdown that is
    really just silence.
    """
    now = time.time() if now is None else now
    done, total = log_steps(text)
    # The TRACKER's window and the tracker's thresholds, not a second rate of
    # this module's own: `window_rate` already refuses the answer when the
    # span is too short or the steps too few, which is what stops a build
    # that paused inside one emulated generator step from reporting 74 hours.
    rate = window_rate(samples or (), mtime)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    progress = None
    if done is not None and total:
        # Same clamp as `fraction`: a bar that reads 100% before the build
        # ends is the reason nobody trusts the next one.
        progress = min(done / float(total), 0.999)
    eta = None
    if rate and total and done is not None and total > done:
        eta = (total - done) / rate
    # A build that said how it ended is not still running, whatever the file's
    # mtime says -- and its `last_at` is the moment IT spoke, not the moment
    # something else appended to the log it happens to share.
    state, ended, said = log_outcome(text, name, now)
    if state:
        # `last` is the last thing THIS BUILD said, not the last line in a
        # file it shares. Without that, a failed webkit reported the neighbour
        # that touched the log next -- `DONE!` -- as its own final word.
        eta, last = None, said
        mtime = ended if ended is not None else mtime
    return {"rung": f"pkg:{name}", "phase": pkg_phase_of(last, "build"),
            "state": state or "running", "pid": None, "elapsed": None,
            "progress": progress, "eta": eta,
            "compile_lines": done or 0, "last": _STAMP.sub("", last.lstrip()),
            "last_at": mtime, "last_age": max(0.0, now - mtime),
            "started": None, "rate": rate,
            "step": step_kind(last),
            "steps": None if done is None else f"{done}/{total}"}


def log_tail(path, limit: int = LOG_TAIL_BYTES):
    """`(text, mtime)` for the end of a log, or `(None, None)`."""
    try:
        stat = os.stat(path)
        with open(path, "rb") as handle:
            if stat.st_size > limit:
                handle.seek(stat.st_size - limit)
            # Reading from a byte offset can land mid-character; the first
            # partial line is dropped by the caller's splitlines anyway.
            return handle.read().decode("utf-8", "replace"), stat.st_mtime
    except OSError:
        return None, None


# How recently a log must have been written for "this build is still going"
# to be the honest reading, and how far ahead of us its timestamp may sit
# before it is a disagreeing clock rather than a build. Bounded at BOTH ends:
# a log dated in the future -- skew, a restored tree, a copied checkout -- is
# not evidence of anything running now.
LOG_FRESH_S = 120.0
CLOCK_SKEW_S = 5.0


def reattach_from_log(log_path, snap, now=None, samples=None):
    """The build `snap` was following, re-read from its log, or None.

    The single answer to "the tracker died but did the BUILD?", shared by
    everything that has to ask: `watch` paints from it, `status` reports it,
    the status line puts it in the chrome. Three copies of this rule would be
    three chances to disagree about whether a build is alive.

    Returns None unless all of it holds: the snapshot froze mid-build (it says
    `running`), its process is gone, and the log either says how the build
    ENDED or is being written right now. A snapshot that ended properly is not
    an orphan, and a quiet log is not a running build.

    The ending outranks the freshness test, and has to: the mtime says only
    that SOMETHING wrote to a log every build in the workspace shares, so a
    fresh one is not evidence this build lives and a stale one is not evidence
    it died. `>>> ERROR: webkit2gtk-6.0: build failed` is evidence, at any age.
    """
    if not snap or (snap.get("state") or "") != "running":
        return None
    if liveness(snap) == "running":
        return None            # the tracker is alive; its own file is better
    text, mtime = log_tail(log_path)
    if text is None:
        return None
    now = time.time() if now is None else now
    if now - mtime < -CLOCK_SKEW_S:
        return None            # a log dated in the future is a broken clock
    name = str(snap.get("rung") or "build").split(":", 1)[-1]
    if now - mtime > LOG_FRESH_S and not log_outcome(text, name, now)[0]:
        return None
    return snapshot_from_log(text, name, mtime, now=now, samples=samples)


# pmbootstrap writes this as the last line of EVERY invocation that finishes,
# and the chroot trailer after it. Both are noise to a reader and decisive to
# this file: if the last thing in the log is the end of an invocation, no
# pmbootstrap is running, whatever the mtime says.
_LOG_ENDED = re.compile(r"(?:^|\])\s*DONE!\s*$")
_LOG_TRAILER = re.compile(r"NOTE: chroot is still active|^\s*$")


def log_invocation_ended(text: str) -> bool:
    """Did the last pmbootstrap invocation in this log finish? PURE.

    The status line and `pkg status` decide "something is building" from a log
    the WHOLE WORKSPACE shares, so its mtime says only that some pmbootstrap
    ran -- `pmbootstrap status`, `index`, a `chroot -- ccache -s`, anything.
    Reported 2026-09-06: the row read

        device-google-taimen  [ unknown ]  --  42m50s  build  · reattached

    where `device-google-taimen` came from a staged APKBUILD an unrelated
    build left behind 42 minutes earlier, and the freshness came from a
    `ccache -s` five seconds before. Every number in it was invented.

    A running build has not printed DONE!. That is the whole test, and it
    cannot produce a false negative the way "are there compile lines in the
    tail" would -- a real build is silent for minutes during packaging.
    """
    for line in reversed((text or "").splitlines()):
        if _LOG_TRAILER.search(line):
            continue
        return bool(_LOG_ENDED.search(line))
    return False


# What only a build writes. Three shapes, because a build is only ever quiet
# in ways one of them still covers: ninja counts steps while compiling,
# abuild's `>>> <pkg>:` banners bracket the phases where ninja says nothing
# (fakeroot, strip, compress, index), and kbuild's prefixes are the kernel
# path's equivalent of both.
#
# All three are anchored at the start of the line, and that is not an
# accident: pmbootstrap prefixes its OWN trace lines with `(pid) [HH:MM:SS] `
# and relays a command's output unprefixed, so the anchor is itself half the
# discrimination. Checked against the reference host's 37 MB log.txt.
# `>>> ERROR:` and `>>> WARNING:` are abuild's DIAGNOSTICS, not its "now
# building <pkg>" banner, and the tools around a build print them too:
# `abuild-sign` failing while pmbootstrap indexed a repo wrote `>>> ERROR:
# failed to sign` into the shared log. That single line was the only
# build-shaped thing in the tail of a FAILED kernel rung, so `names_a_build`
# said yes, the staged APKBUILD from two days earlier supplied a name, and the
# status line showed `webkit2gtk-6.0 ... 42h05m . reattached` for a build
# nobody had started. Reported twice, with screenshots, 2026-09-08.
_ABUILD_BANNER = re.compile(r"^>>> (?!ERROR:|WARNING:)\S+?\*?: \S")
# ...but tolerate pmbootstrap's own `(pid) [HH:MM:SS] ` prefix in front of one.
# On the reference host's 37 MB log.txt every one of 188276 ninja lines and
# 7942 abuild banners is bare -- pmbootstrap stamps the lines it writes itself
# and relays a command's output untouched. Stripping it anyway is the cheap
# side of the trade: a shape that stops being recognised blanks a real build,
# and that is the failure this file has already been through once.
_LOG_PREFIX = re.compile(r"^\(\d+\)\s+\[[\d:]+\]\s*")


def names_a_build(text: str) -> bool:
    """Does this log tail carry a line only a BUILD produces? PURE.

    The missing third fact. `staged_build_name` says WHICH package was staged
    and `log.txt`'s mtime says something wrote it just now -- and neither is
    evidence that the writer is a build. The log is shared by the whole
    workspace: `pmbootstrap status`, `pmbootstrap log`, a `chroot -- ccache -s`
    and an agent sitting in an open `pmbootstrap chroot` all touch it. On
    2026-09-07 that combination reported a `webkit2gtk-6.0` build at 2h30m,
    with a name from a staging directory the day before and a freshness from a
    chroot session with nothing to do with it.

    ANY line in the tail, not the last: packaging is legitimately silent for
    minutes, and the tail still holds what the build said before it went
    quiet. Requiring the newest line to be build-shaped would call a healthy
    build a phantom, which is the failure this repo has already paid for in
    the other direction.
    """
    for line in (text or "").splitlines():
        line = _LOG_PREFIX.sub("", line)
        if ninja_progress(line) or is_compile_line(line):
            return True
        if _ABUILD_BANNER.match(line):
            return True
    return False


def live_build_from_log(log_path, name, started=None, now=None, samples=None):
    """A snapshot for a build NOTHING ever published a status file for, or None.

    `reattach_from_log` answers a narrower question -- "the tracker died MID
    BUILD, did the build?" -- and refuses anything whose snapshot is not
    `running`. That leaves a real case with no answer at all: a build started
    through `sandbox shell --command`, or simply one that began after the last
    tracked build wrote `done`. Nothing was published for it and nothing ever
    will be, so every consumer that asks the snapshot is blind to it. Reported
    2026-09-06 as "the status line shows nothing while `pkg watch` shows a
    two-hour webkit build" -- watch saw it only via a podman `ps`, which the
    status line cannot afford at a two-second refresh.

    The division of evidence is the whole point. `name` comes from the
    buildroot (`porthole_buildroot.staged_build_name`), because the log's
    naming banner is written once at the start and is long outside the tail.
    LIVENESS comes from the log's mtime, because the staged APKBUILD outlives
    the build that wrote it. Neither fact alone is enough and neither is a
    guess.
    """
    if not name:
        return None                # never guess a name into somebody's chrome
    now = time.time() if now is None else now
    text, mtime = log_tail(log_path)
    if text is None:
        return None
    # Bounded at both ends, like every other freshness test here: a quiet log
    # is not a running build, and a log dated in the future is a broken clock.
    if not -CLOCK_SKEW_S <= now - mtime <= LOG_FRESH_S:
        return None
    # The log says how this build ENDED. A fresh mtime after that is somebody
    # else touching a log the whole workspace shares.
    if log_outcome(text, name, now)[0]:
        return None
    # ...and the same is true when the log says nothing about THIS name but
    # the invocation that was writing it has finished. `log_outcome` can only
    # answer for the package it is given, and `name` here comes from a staged
    # APKBUILD that outlives its build -- so on a mismatch it finds no ending
    # and the row invents one.
    if log_invocation_ended(text):
        return None
    # ...and the same is true when the log tail was not written by a build at
    # all. The two facts this function has -- a staged package name and a
    # fresh mtime -- are each true of things that are not builds: the staged
    # APKBUILD outlives the build that put it there, and `log.txt` is shared
    # by every pmbootstrap invocation in the workspace, including an agent
    # sitting in an open `pmbootstrap chroot`. Together they reported a
    # webkit2gtk-6.0 build at 2h30m with nothing running.
    #
    # Declining here costs the first moments of a genuinely untracked build,
    # before ninja or abuild has said anything. That is the right way round:
    # a row that appears a few seconds late is a delay, and a row that names a
    # build nobody started is a lie somebody acts on.
    if not names_a_build(text):
        return None
    snap = snapshot_from_log(text, name, mtime, now=now, samples=samples)
    if started:
        # The one thing a log tail genuinely cannot know. The staged APKBUILD's
        # mtime is when pmbootstrap put it there, which is when this build
        # began.
        snap["started"] = started
        snap["elapsed"] = max(0.0, now - started)
    return snap


def orphaned(snap, foreign: str) -> bool:
    """Is `foreign` the very build this status file was following?

    Two different facts wear the same symptom -- a live build and a status
    file nobody is updating -- and the answer decides which sentence is true:

      the tracker died      `porthole aports build webkit2gtk-6.0` published
                            this file, its process was killed, and the build
                            kept running because the workspace outlives the
                            client that started it. The numbers here are real
                            but frozen, and the flock died with the holder.
      it never came through  a `sandbox shell --command` build. Nothing was
                            ever published for it and nothing ever will be.

    `rung` is `pkg:<name>`; the probe reports the bare `<name>`. Pure, so the
    wording each case gets is testable without a container.
    """
    rung = (snap or {}).get("rung") or ""
    return bool(foreign) and rung.split(":", 1)[-1] == foreign


# How often a reattached watch asks the workspace whether the build is still
# there. Not every tick: that question costs a `podman exec` and the answer
# changes once. The log's own mtime is the free signal in between -- a build
# that is writing is a build that is running -- so the probe is only spent
# when the log has gone quiet.
PROBE_AFTER_S = 30.0




@contextlib.contextmanager
def quiet_terminal(enabled: bool):
    """No echo, no cursor, for as long as a block is being repainted.

    Three reported defects, one cause. A watch repaints by walking the cursor
    back a KNOWN number of rows -- so anything else that writes to the
    terminal moves the ground under it. Press Enter while watching and the
    screen scrolls one line: the walk-back now lands a row short, the repaint
    lands on top of the previous frame instead of over it, and the block
    duplicates itself down the screen. Every keystroke echoed into the middle
    of the bar does the same thing on a smaller scale, and the cursor itself
    sits blinking in the middle of the display.

    So while the block owns the screen, the terminal stops echoing (the keys
    still reach us -- `setcbreak` leaves ISIG alone, so Ctrl-C is exactly as
    interruptible as before) and the cursor is hidden. Both are restored on
    the way out, including out through Ctrl-C, because a terminal left with
    ECHO off is a broken shell and that is a far worse bug than the one this
    fixes.
    """
    if not enabled:
        yield
        return
    try:
        import termios
        import tty as ttymod
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
    except Exception:  # noqa: BLE001 -- no tty, no termios, nothing to do
        yield
        return
    try:
        ttymod.setcbreak(fd)
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        finally:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()


def paced(interval: float, tty: bool, ndjson: bool, repaint) -> None:
    """Wait one poll, keeping the display moving while we do.

    Reading the log ten times a second to animate one glyph would be silly;
    painting ten times a second between reads is not, and it is the
    difference between a spinner that spins and one that stutters once per
    second. Off a terminal there is nothing to animate and this is a plain
    sleep.
    """
    if not tty or ndjson:
        time.sleep(interval)
        return
    end = time.time() + interval
    while True:
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(1.0 / SPIN_FPS, left))
        repaint()


def footer_of(reason: str, tty: bool, width: int = 0) -> str:
    """The block's quietest row: why this watch is where it is, and how to
    leave it. Pure.

    Both halves used to be printed at the TOP, in full brightness, before the
    bar -- so the first thing the eye met was two lines of chrome, and on a
    narrow terminal the longer one wrapped INTO the bar. They are the least
    important text on screen: read once, then ignored. So they are last, they
    are grey, and they are one line that gets clipped rather than wrapped.

    Terminal only: a pipe has no keyboard, and an agent reading ndjson gets
    nothing out of prose but a parse error.
    """
    if not tty:
        return ""
    reason = reason.strip()
    # The reassuring clause is the first thing to go when the terminal is
    # narrow: "Ctrl-C stops watching" already answers the question, and the
    # reason -- which explains why this display looks unusual at all -- is
    # the half a reader cannot reconstruct for themselves.
    for hint in ("Ctrl-C stops watching, the build keeps running",
                 "Ctrl-C stops watching"):
        line = "  \u00b7  ".join([part for part in (reason, hint) if part])
        if len(line) + 2 <= (width or len(line) + 2):
            return line
    return reason or "Ctrl-C stops watching"


def reattach_banner(snap, name: str, status_name: str) -> str:
    """Why this watch is reading a log instead of a status file. Pure.

    Two different histories reach the same place, and the reader has to be
    told which one they are in -- one of them means the buildroot mutex is
    lying to everyone else on the machine. SHORT, because this is footer text
    now: it shares one clipped line with the exit hint, and the full account
    of the lock is in the final report and in the trap note.
    """
    if orphaned(snap, name):
        return "reattached \u00b7 no tracker \u00b7 lock released"
    return "reattached \u00b7 not started here \u00b7 no lock held"


def reattach(log_path, name: str, probe, interval: float, out,
             ndjson: bool = False, tty: bool = False, banner: str = "",
             now=None) -> dict:
    """Follow a build through its log until it ends. Returns the last snapshot.

    This is the answer to "why can I not just join it?". Nothing about a
    build's progress lives in the process that started it -- the numbers are
    in a file that the workspace keeps writing whether or not anyone is
    watching. So a watcher attaches to the FILE, and a tracker that died
    (outage, timeout, closed session) costs a reader nothing but the elapsed
    time it can no longer know.

    Read-only, deliberately: it publishes no status file and takes no lock.
    The build it found belongs to whoever started it, and a watcher that
    started writing on their behalf would be inventing an owner.
    """
    paint, clear_block = block_painter(out)

    # On a terminal the explanation belongs in the block's footer, dim and
    # out of the way, where it is repainted with everything else and cannot
    # wrap into the bar. Off a terminal there is no block to put it in and no
    # keyboard to tell about, so it is said once, plainly, and never again.
    foot = ""
    if banner and not ndjson:
        if tty:
            foot = footer_of(banner, tty=True, width=term_width())
        else:
            out(banner + "\n")
    samples = collections.deque(maxlen=4096)
    snap, last_note, asked = {}, 0.0, time.time()
    moved, steps = time.time(), None
    while True:
        text, mtime = log_tail(log_path)
        if text is None:
            break
        clock = time.time() if now is None else now()
        if log_steps(text)[0] is not None:
            sample = (mtime, log_steps(text)[0])
            if not samples or samples[-1] != sample:
                samples.append(sample)
        snap = snapshot_from_log(text, name, mtime, now=clock,
                                 samples=samples)
        if ndjson:
            out(json.dumps({**snap, "note": banner}) + "\n")
        elif tty:
            paint(watch_lines(snap, now=clock))
        elif clock - last_note > max(interval, 15):
            last_note = clock
            out("\n".join(watch_lines(snap, now=clock)) + "\n")
        # The build said how it went. Nothing a process list could add to
        # that, and waiting for one is how this loop used to sit on a
        # finished build until somebody Ctrl-C'd it.
        if (snap.get("state") or "running") != "running":
            break
        # Otherwise the build has to have stopped ADVANCING before the
        # workspace is worth a `podman exec ps` -- which is not the same
        # question as whether the log was touched. log.txt belongs to the
        # workspace, so an unrelated `pmbootstrap chroot` refreshes its mtime
        # and used to reset this gate: on a machine anybody was using, the
        # probe never fired and the watch never reached its verdict. The step
        # count is this build's own signal, and packaging is legitimately
        # still for minutes -- which is exactly when the probe is worth its
        # half second.
        if steps != snap.get("steps"):
            moved, steps = clock, snap.get("steps")
        if clock - moved >= PROBE_AFTER_S and clock - asked >= PROBE_AFTER_S:
            asked = clock
            if not probe():
                # One last read before leaving. The lines a build writes as
                # it finishes -- the final link, abuild's own markers -- land
                # between the previous tick and the process disappearing, and
                # a final report that stops short of them describes a build
                # that was still going.
                text, mtime = log_tail(log_path)
                if text is not None:
                    snap = snapshot_from_log(text, name, mtime, now=clock,
                                             samples=samples)
                break
        # `now()` per repaint, not the tick's `clock`: reusing one timestamp
        # for all ten frames of a second is why the spinner stood still.
        paced(interval, bool(tty), ndjson,
              lambda: paint(watch_lines(snap, now=now() if now else None,
                                        footer=foot)))
    # `clear_block` is a no-op when nothing was painted, so the count it
    # used to be guarded on lives inside it now.
    if tty and not ndjson:
        clear_block()
    return snap


def watch(rundir, status_name: str, interval: float, out, ndjson: bool = False,
          tty=None, start_hint: str = "", probe=None, log=None,
          verdict=None) -> int:
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
    Every string this function emits carries its own cursor handling -- on
    a tty, one write per repaint containing the walk back up to the top of
    the block (`\\033[NA`), a `\\r\\033[2K` per row and no trailing newline;
    a trailing `\\n` for everything else -- so `out` itself never has to
    know which mode is active.

    `ndjson=True` emits one JSON object per update instead of a bar, and
    skips the final `status_report` block: an agent can consume a stream: it
    cannot consume a redrawn terminal.

    ONE SHAPE, always -- this was a discriminated union (a waiting object with
    only `state`/`note`/`previous`, a live object with the full snapshot) and
    an agent doing `json.loads(line)["rung"]` KeyErrored on line one, in
    exactly the "somebody else's run just finished" case this feature exists
    to handle. Every emitted object now carries the same key set --
    `rung`, `phase`, `state`, `pid`, `elapsed`, `progress`, `eta`,
    `compile_lines`, `last`, `started`, `note` (plus `rate` when the tracker
    reports one) -- so `obj["rung"]`, `obj["progress"]`, `obj["note"]` are
    always safe to read, on EVERY object, not just the waiting ones. `state`
    is still the discriminator: `"waiting"` means no run is live right now
    (`note` carries the human sentence, and the rest of the fields are the
    PREVIOUS run's, or all `None` if nothing has ever published here);
    anything else is a real snapshot's own `state` (`"running"`, `"done"`,
    `"failed"`, ...) and `note` is `""` -- there is nothing to say about a
    run that is speaking for itself through the rest of the fields. A `None`
    value means genuinely not known yet, never a missing key.

    `start_hint`, if given, is the exact command that starts a run of this
    kind (e.g. "porthole pkg build <aport>") -- only the CALLER knows that,
    so this function never guesses one from `status_name`. Without it, the
    "nothing has ever published here" error falls back to generic wording.

    `probe`, if given, answers "is something building that never published
    here?" -- it returns a name, or "". Only the caller can ask that (it means
    a `podman exec ps` in the workspace), and it is asked ONCE, only when
    there is nothing live to attach to.

    `log` is that build's log file -- a path, or a callable returning one --
    and it is what turns "there is a build I cannot follow" into "there is a
    build, here it is". With it, a named foreign build is followed through
    `reattach` instead of refused. `verdict(name, snap)` then decides the
    exit code from the ARTIFACT once the build ends, because a reattached
    watcher has no tracker to have written `done` or `failed`; without one
    the run is reported and the exit is non-zero, since nothing verified it.

    Polling a file, not sleeping through the run: the sleep here is between
    reads of a real signal, which is what brain/laws/poll-never-sleep.md asks
    for rather than what it forbids.
    """
    path = pathlib.Path(rundir) / status_name
    if tty is None:
        tty = os.isatty(1)

    # Every read goes through here, so this is the one place a derived rate
    # has to be added for the waiting loop, the live loop and the final
    # report to agree about one build's speed.
    observed = collections.deque(maxlen=4096)

    def snapshot():
        # Absent, or read mid-rename: both are "nothing to attach to yet",
        # not an error -- the writer is atomic, so the next read succeeds.
        if not path.exists():
            return None
        try:
            snap = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return observed_rate(snap, observed, time.time())

    # `watch` paints a BLOCK now -- the numbers, then what the run is doing --
    # so a redraw has to walk back up to the top of it. A bare `\r` would
    # rewrite only the bottom row and leave the one above it frozen on screen,
    # which is the very appearance of staleness this change exists to remove.
    paint, clear_block = block_painter(out)

    def unpaint():
        clear_block()

    foot = footer_of("", bool(tty) and not ndjson, term_width())
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

    # A build that did not come through porthole -- `sandbox shell --command
    # 'pmbootstrap build ...'`, a hand-rolled podman exec -- takes no lock and
    # writes no status file. The newest snapshot is then the PREVIOUS run's,
    # and every word this loop says about it is true and completely wrong
    # about what the machine is doing: measured, a watch during a live webgtk
    # build reported "finished 24m ago" and exited 0. So when there is nothing
    # live to attach to, ask before concluding nothing is happening.
    if probe and (snap is None or is_stale(snap)):
        foreign = probe() or ""
        if foreign and log:
            # There IS something to follow -- it is just not in the status
            # file. Everything below this point (the two Bails) is what
            # happens only when the log cannot be reached at all.
            path = log() if callable(log) else log
            text, _ = log_tail(path) if path else (None, None)
            if text is not None:
                snap = reattach(path, foreign, probe, interval, out,
                                ndjson=ndjson, tty=tty,
                                banner=reattach_banner(snap, foreign,
                                                       status_name))
                if verdict:
                    return verdict(foreign, snap)
                if not ndjson:
                    head, rows = status_report(snap, alive=lambda pid: False)
                    out("  " + head + "\n")
                    for label, value in rows:
                        out(f"  {label:<10}  {value}\n")
                return EX_FAIL
        if foreign and orphaned(snap, foreign):
            stopped = finished_at(snap)
            frozen = fmt_dur(time.time() - stopped) if stopped else "a while"
            raise Bail(
                f"{foreign} is still building, but nothing is following it "
                f"any more -- the porthole run that published {status_name} "
                f"is gone, and its last numbers are {frozen} old",
                EX_FAIL,
                f"the build outlived its tracker: it runs in the workspace, "
                f"which survives the client that started it. Its own output "
                f"is the only live witness now -- and the buildroot lock died "
                f"with the tracker, so nothing else may build until it ends.")
        if foreign:
            raise Bail(
                f"{foreign} is building, but it never published "
                f"{status_name} -- there is nothing here to follow",
                EX_FAIL,
                f"it was started outside `porthole pkg build`, so it took no "
                f"lock either. Follow its own output, or wait for it and "
                + (start_hint or "start the next one through porthole"))

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
            paint([line])
        elif last_note == 0.0 or time.time() - last_note > max(interval, 15):
            last_note = time.time()
            out(line + "\n")
        time.sleep(0.25 if snap is None else interval)
        snap = snapshot()
    if tty and not ndjson:
        unpaint()

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
                # `note` is on every object, not just the waiting ones --
                # `obj["note"]` must never KeyError on a live object either.
                # Empty here: there is nothing to SAY about a run that is
                # speaking for itself via the rest of the fields.
                out(json.dumps({**snap, "note": snap.get("note", "")}) + "\n")
            elif tty:
                # `watch_lines` measures the terminal itself, on every
                # repaint, so a resized window stops wrapping on the next
                # tick rather than at the next run.
                paint(watch_lines(snap))
            elif time.time() - last_note > max(interval, 15):
                # Off a tty this is somebody's log -- a detached build's spawn
                # log, or CI. Two lines every fifteen seconds, on the same
                # throttle the single line used to have: the activity line is
                # the reason to read the log at all, and doubling a line
                # nobody prints more than four times a minute is not a flood.
                last_note = time.time()
                out("\n".join(watch_lines(snap)) + "\n")
            if live != "running":
                break
            paced(interval, bool(tty), ndjson,
                  lambda: paint(watch_lines(snap, footer=foot)))
            snap = snapshot() or snap
        if tty and not ndjson:
            unpaint()

    if not ndjson:
        head, rows = status_report(snap)
        out("  " + head + "\n")
        for label, value in rows:
            out(f"  {label:<10}  {value}\n")
    return EX_OK if liveness(snap) == "done" else EX_FAIL
