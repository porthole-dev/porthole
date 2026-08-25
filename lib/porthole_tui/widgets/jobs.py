# SPDX-License-Identifier: MIT
"""The drawer: what is running, without taking the screen.

Collapsed it is one line, so a build is visible while you read a note.
Expanded it is the stream. The curses console had neither -- it left curses
entirely and waited for a keypress to come back.

No BINDINGS here on purpose (ruling R20): a Binding declared on a WIDGET only
resolves while focus sits inside that widget's own subtree, and JobDrawer
docks outside #content -- with the normal focus on a catalogue's #rows list,
a key bound here would never appear in the resolution chain at all. Measured
with a pilot probe before writing this. ctrl+j/ctrl+c/ctrl+r are bound on
MainScreen instead, which is always in the chain, and MainScreen's actions
call the plain methods below.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Label, RichLog


class JobDrawer(Vertical):
    expanded = reactive(False)

    MARK = {"running": "*", "done": "+", "cancelled": "x", "queued": "."}

    def __init__(self, **kw):
        super().__init__(**kw)
        self.job = None
        self._seen = 0
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Label("no job running", id="job-line")
        yield RichLog(id="job-log", highlight=False, markup=False)

    def on_mount(self) -> None:
        # Created paused, not merely never-started, because a widget-level
        # timer ticking ten times a second for the life of the app -- copying
        # up to 20,000 strings on every tick -- outlives every job it was
        # created for (reviewer's Minor 1). attach()/_drain() resume and
        # re-pause it around each job's actual lifetime.
        self._timer = self.set_interval(0.1, self._drain, pause=True)

    def text(self):
        return "\n".join(self.job.lines) if self.job else ""

    def attach(self, job) -> None:
        self.job = job
        self._seen = 0
        self.query_one("#job-log", RichLog).clear()
        if self._timer is not None:
            self._timer.resume()
        self._drain()

    def _drain(self) -> None:
        if self.job is None:
            return
        # `len(job.lines)` measures the RING, not the STREAM. Job.lines is a
        # deque(maxlen=20000): once it saturates, len() is pinned at maxlen
        # forever and a `len(lines) > self._seen` guard never fires again --
        # the log freezes at line 20000 while the collapsed label keeps
        # ticking, so nothing LOOKS wrong. A kernel build is exactly the case
        # that exceeds it (ruling R25). `produced` only ever grows, so it
        # survives eviction and tells "nothing new" from "more than the ring
        # can hold" apart.
        total = self.job.produced
        if total > self._seen:
            log = self.query_one("#job-log", RichLog)
            survived = list(self.job.lines)
            new = total - self._seen
            if new > len(survived):
                # More was produced than the ring can hold. Say so rather
                # than quietly skipping: a log that drops lines without
                # admitting it is worse than one that scrolls.
                log.write("... {} lines scrolled past the {}-line buffer".format(
                    new - len(survived), self.job.lines.maxlen))
                new = len(survived)
            for line in survived[len(survived) - new:]:
                log.write(line)
            self._seen = total
        lines = list(self.job.lines)
        tail = lines[-1][:44] if lines else ""
        self.query_one("#job-line", Label).update("{} {}  {:.0f}s  {}".format(
            self.MARK.get(self.job.state, "?"), self.job.command[:38],
            self.job.duration(), tail))
        # NOT "!= running": attach() calls _drain() synchronously, before
        # the job's own asyncio task has had a turn to flip state to
        # "running", so a job is still "queued" on that first call. Pausing
        # on that condition paused the timer one tick after resuming it --
        # measured (a job would never drain past its first line). Pause only
        # on the two states JobManager._finish actually sets.
        if self.job.state in ("done", "cancelled") and self._timer is not None:
            self._timer.pause()

    def watch_expanded(self, _old, new) -> None:
        self.set_class(new, "-expanded")

    def action_toggle(self) -> None:
        self.expanded = not self.expanded

    def action_cancel(self) -> None:
        if self.job is not None and self.job.state == "running":
            self.job.cancel()
            self.notify("cancelling")

    def action_rerun(self) -> None:
        """Re-run always goes back through the boundary.

        The command already ran once, which proves nothing: it may have been
        confirmed then, and confirming once must not buy a second run.
        """
        if self.job is not None:
            self.screen.launch(self.job.command, safe=False)
