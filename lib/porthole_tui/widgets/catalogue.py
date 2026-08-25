# SPDX-License-Identifier: MIT
"""One filterable list, so a pane cannot be written without a viewport or a
filter by forgetting to add them.

Written after each curses pane had its own `for i, item in enumerate(...)` with
a `break`, which is not a viewport -- it is a truncation that looks like one
until the list is longer than the terminal.

A count line sits above the list for the same reason the old console's
`_list.scrollbar()` rendered `"12-30 of 55"` instead of a bar: "am I seeing
all of it" is a question a number answers and a one-column glyph does not.
Textual's own scrollbar shows proportion, never how many a filter removed.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, Label, ListItem, ListView


class Catalogue(Vertical):
    BINDINGS = [
        Binding("slash", "filter", "filter", key_display="/"),
        Binding("escape", "clear_filter", "clear", show=False),
    ]

    snapshot = reactive(None)
    query = reactive("")

    empty_message = "nothing here yet"
    noun = "items"          # subclasses name what they list: "tools", "notes"...

    class Chosen(Message):
        def __init__(self, payload):
            self.payload = payload
            super().__init__()

    def __init__(self, **kw):
        super().__init__(**kw)
        self._payloads = []

    def rows(self, snap):
        """[(label, payload), ...] for this snapshot and self.query."""
        raise NotImplementedError

    def visible_rows(self):
        return self.rows(self.snapshot)

    def count_label(self) -> str:
        """`"98 tools"`, or `"7 of 98 tools  ·  filter: firstpaint"` when
        filtered. Pure: no query_one, no rendered output, so it is testable
        on its own. Diffs self.query against a blanked copy via
        `set_reactive` rather than a second parallel "unfiltered" method,
        so a subclass's `rows()` stays the single place filtering happens.
        """
        query = self.query
        shown = len(self.rows(self.snapshot))
        if not query:
            return "{} {}".format(shown, self.noun)
        self.set_reactive(Catalogue.query, "")
        try:
            total = len(self.rows(self.snapshot))
        finally:
            self.set_reactive(Catalogue.query, query)
        return "{} of {} {}  ·  filter: {}".format(
            shown, total, self.noun, query)

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter", id="filter")
        yield Label("", id="count", classes="catalogue--count")
        yield ListView(id="rows")

    async def on_mount(self) -> None:
        await self._repopulate()
        # `mount()` is asynchronous, so the caller's `widget.focus()` in
        # `_show()` (screens/main.py) races the compose of #rows and loses:
        # this container itself is not focusable, so that call is a silent
        # no-op. Doing it here instead runs once compose has actually
        # finished, and lands keyboard focus on the list so arrow keys work
        # immediately, without a tab first.
        self.query_one("#rows", ListView).focus()

    async def watch_snapshot(self, _old, _new) -> None:
        await self._repopulate()

    async def watch_query(self, _old, _new) -> None:
        await self._repopulate()

    async def _repopulate(self) -> None:
        """Async because `ListView.clear()` is.

        It returns an AwaitRemove and the removal has NOT happened when it
        returns. _repopulate runs twice on entry -- once from on_mount, once
        from watch_snapshot when MainScreen._show assigns the snapshot -- so
        dropping that awaitable left the first population in the DOM while
        the second appended beside it: 95 tools rendered as 190 rows, empty
        states printed their message twice, the count label said 95 while
        showing 190, and on_list_view_selected's `index < len(self._payloads)`
        guard made Enter do nothing at all on every row past the first
        population. Textual permits async on_mount and async watchers.
        """
        try:
            listing = self.query_one("#rows", ListView)
            count = self.query_one("#count", Label)
        except Exception:  # noqa: BLE001
            return                      # not mounted yet
        await listing.clear()
        rows = self.visible_rows()
        self._payloads = [payload for _label, payload in rows]
        count.update(self.count_label())
        if not rows:
            listing.append(ListItem(Label(self.empty_message, classes="empty")))
            return
        for label, _payload in rows:
            listing.append(ListItem(Label(label)))

    def action_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_clear_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.value = ""
        self.query = ""
        self.query_one("#rows", ListView).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.query = event.value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the filter moves to the list WITHOUT closing the filter.

        The old console's filter swallowed enter to leave filter mode, so
        opening a filtered result took two keystrokes that looked identical
        and only the second did anything. The query is left as it is: moving
        focus is not the same as clearing what was typed.
        """
        if event.input.id == "filter":
            self.query_one("#rows", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is not None and index < len(self._payloads):
            self.post_message(self.Chosen(self._payloads[index]))
