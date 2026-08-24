# SPDX-License-Identifier: MIT
"""The main screen: rail, content, drawer.

One screen with a swappable content region rather than six screens, because
the rail must stay on screen across every section -- pushing a screen per
section would take the spine away exactly when it is most useful.

The catalogue widgets (PortView, DeviceList, ToolList, NoteList) are wired in
by a later task. Until then every section -- including PORT -- shows the same
labelled placeholder, which is honest: there is nothing to look at yet, and an
empty pane reads as a broken tool where a sentence does not.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Label

from ..widgets.rail import Rail


class MainScreen(Screen):
    BINDINGS = [
        Binding("tab", "focus_next", "focus", show=False),
        Binding("shift+tab", "focus_previous", "focus", show=False),
        Binding("r", "refresh", "refresh"),
        Binding("question_mark", "help", "keys", key_display="?"),
        Binding("q", "quit", "quit"),
    ]

    section = reactive("port")

    def __init__(self, sections, **kw):
        super().__init__(**kw)
        self.sections = sections
        self.reader_open = False
        # Populated by a later task once the catalogue widgets exist.
        self.WIDGETS = {}
        for index, (key, _title) in enumerate(sections, start=1):
            self._bindings.bind(str(index), "section('{}')".format(key),
                                show=False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Rail(self.sections, id="rail")
            yield Vertical(id="content")
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
