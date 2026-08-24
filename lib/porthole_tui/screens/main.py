# SPDX-License-Identifier: MIT
"""The main screen: rail, content, drawer.

One screen with a swappable content region rather than six screens, because
the rail must stay on screen across every section -- pushing a screen per
section would take the spine away exactly when it is most useful.

Sections 1-4 (port, devices, tools, brain) have real catalogue widgets.
Sections 5-6 (logs, jobs) still show the placeholder below -- their widgets
land in a later task, and an empty pane reads as a broken tool where a
sentence does not.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Label

from ..widgets.brain import NoteList
from ..widgets.devices import DeviceList
from ..widgets.jobs import JobDrawer
from ..widgets.port import PortView
from ..widgets.rail import Rail
from ..widgets.tools import ToolList


class MainScreen(Screen):
    # No tab/shift+tab bindings here: Screen already ships them qualified as
    # 'app.focus_next' / 'app.focus_previous'. Redeclaring them unqualified
    # replaces those correct defaults with ones that resolve against THIS
    # class -- which has no action_focus_next -- and silently does nothing.
    # Ruling R21: measured dead (focus never moved) before this was deleted.
    BINDINGS = [
        Binding("r", "refresh", "refresh"),
        Binding("question_mark", "help", "keys", key_display="?"),
        Binding("q", "quit", "quit"),
        # The drawer's own keys live here, not on JobDrawer: a Binding on a
        # WIDGET only resolves while focus sits inside that widget's own
        # subtree, and JobDrawer docks outside #content -- with the normal
        # focus on a catalogue's #rows, a key bound on JobDrawer itself would
        # never appear in the resolution chain (ruling R20, measured with a
        # pilot probe). MainScreen is always in the chain, so the actions
        # below just delegate into the drawer.
        Binding("ctrl+j", "toggle_drawer", "job drawer"),
        # ctrl+c is otherwise claimed by Textual itself: App binds it to
        # help_quit (a "press q to quit" nag) and Screen binds it to
        # copy_text. Declaring it again here, in MainScreen's own BINDINGS,
        # wins over both -- confirmed with a pilot probe, not assumed.
        Binding("ctrl+c", "cancel_job", "cancel job", show=False),
        Binding("ctrl+r", "rerun_job", "re-run", show=False),
    ]

    section = reactive("port")

    def __init__(self, sections, **kw):
        super().__init__(**kw)
        self.sections = sections
        self.reader_open = False
        # Logs and jobs keep the placeholder below until a later task.
        self.WIDGETS = {"port": PortView, "devices": DeviceList,
                        "tools": ToolList, "brain": NoteList}
        for index, (key, _title) in enumerate(sections, start=1):
            self._bindings.bind(str(index), "section('{}')".format(key),
                                show=False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Rail(self.sections, id="rail")
            yield Vertical(id="content")
        yield JobDrawer(id="drawer")
        yield Footer()

    def on_mount(self) -> None:
        self._show(self.section)
        self.app.store.refresh()
        self.set_interval(0.4, self._poll)

    def _show(self, key) -> None:
        content = self.query_one("#content")
        content.remove_children()
        factory = self.WIDGETS.get(key)
        if factory is None:
            # Every section is a placeholder until the catalogue widgets land.
            # Saying so beats drawing an empty pane, which reads as broken.
            # Focusable, like the real widgets it stands in for: focus must
            # never have nowhere to land.
            label = Label("{} arrives in a later release".format(key),
                          classes="empty")
            label.can_focus = True
            content.mount(label)
            label.focus()
            return
        widget = factory()
        content.mount(widget)
        widget.snapshot = self.app.store.snapshot
        widget.focus()

    def watch_section(self, _old, new) -> None:
        if self.is_mounted:
            self._show(new)
            self.query_one(Rail).section = new

    def _poll(self) -> None:
        """Publish the snapshot into the widgets when the worker replaces it.

        Polling a stamp rather than a callback: the Store is thread-based, and
        a worker thread calling into Textual widgets directly is the class of
        bug the old model's comments warn about.
        """
        snap = self.app.store.snapshot
        rail = self.query_one(Rail)
        if rail.snapshot is None or rail.snapshot.stamp != snap.stamp:
            rail.snapshot = snap
            for widget in self.query("#content > *"):
                if hasattr(widget, "snapshot"):
                    widget.snapshot = snap

    def action_section(self, key: str) -> None:
        self.section = key

    def action_quit(self) -> None:
        # A key bound at Screen level dispatches to a method on the SCREEN,
        # not the App -- Screen has no action_quit of its own, so "q" pressed
        # nothing at all until this existed. Delegate explicitly.
        self.app.exit()

    def action_refresh(self) -> None:
        self.app.store.refresh()
        self.notify("refreshing")

    def action_help(self) -> None:
        from .help import HelpScreen
        self.app.push_screen(HelpScreen())

    def action_toggle_drawer(self) -> None:
        self.query_one(JobDrawer).action_toggle()

    def action_cancel_job(self) -> None:
        self.query_one(JobDrawer).action_cancel()

    def action_rerun_job(self) -> None:
        self.query_one(JobDrawer).action_rerun()

    def launch(self, command, safe=False) -> None:
        """Run a porthole command, with the confirmation boundary applied.

        Refuses anything that does not start with `porthole `: a milestone's
        `how` field is sometimes prose ("read the panel datasheet"), and prose
        must never reach a shell.
        """
        from ..safety import is_risky, needs_confirmation
        from .confirm import ConfirmRun
        if not command or not command.startswith("porthole "):
            self.notify("that step is a description, not a command",
                        severity="warning")
            return
        if not needs_confirmation(command, safe):
            self._spawn(command)
            return

        def answered(agreed):
            if agreed:
                self._spawn(command)

        self.app.push_screen(ConfirmRun(command, is_risky(command)), answered)

    def _spawn(self, command) -> None:
        job = self.app.jobs.spawn(command)
        self.query_one(JobDrawer).attach(job)
        self.notify("running: {}".format(command))

    def on_catalogue_chosen(self, event) -> None:
        from ..content import for_milestone, for_note, for_tool
        from .reader import Reader
        payload = event.payload
        # Explicit shapes, not duck-typing (ruling R24). Tool and Note
        # currently have disjoint attributes so hasattr() on one happened to
        # work, but that is luck: give Tool a .body and every tool would
        # silently open as a note. A device must be an actual str, and
        # anything unrecognised is refused rather than formatted into a
        # command -- launch() checks the PREFIX, not the sense, so
        # "porthole use {'id': ...}" would sail straight through its gate.
        if isinstance(payload, dict) and "phase" in payload:
            doc = for_milestone(payload)
        elif isinstance(payload, str):
            self.launch("porthole use {}".format(payload), safe=True)
            return
        elif hasattr(payload, "body") and hasattr(payload, "meta"):
            doc = for_note(self.app.root, payload.id)
        elif hasattr(payload, "summary") and hasattr(payload, "needs"):
            doc = for_tool(self.app.root, payload.name)
        else:
            self.notify("cannot open that selection", severity="warning")
            return

        def closed(command):
            self.reader_open = False
            if command:
                self.launch(command, safe=getattr(doc, "safe", False))

        self.reader_open = True
        self.app.push_screen(Reader(doc), closed)
