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

    def text(self):
        return "\n".join(self.job.lines) if self.job else ""

    def attach(self, job) -> None:
        self.job = job
        self._seen = 0
        self.query_one("#job-log", RichLog).clear()
        if self._timer is None:
            self._timer = self.set_interval(0.1, self._drain)
        self._drain()

    def _drain(self) -> None:
        if self.job is None:
            return
        lines = list(self.job.lines)
        if len(lines) > self._seen:
            log = self.query_one("#job-log", RichLog)
            for line in lines[self._seen:]:
                log.write(line)
            self._seen = len(lines)
        tail = lines[-1][:44] if lines else ""
        self.query_one("#job-line", Label).update("{} {}  {:.0f}s  {}".format(
            self.MARK.get(self.job.state, "?"), self.job.command[:38],
            self.job.duration(), tail))

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
