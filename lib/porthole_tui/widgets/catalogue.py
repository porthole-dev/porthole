# SPDX-License-Identifier: MIT
"""One filterable table, so a pane cannot be written without a viewport or a
filter by forgetting to add them.

Written after each curses pane had its own `for i, item in enumerate(...)` with
a `break`, which is not a viewport -- it is a truncation that looks like one
until the list is longer than the terminal.

A count line sits above the table for the same reason the old console's
`_list.scrollbar()` rendered `"12-30 of 55"` instead of a bar: "am I seeing
all of it" is a question a number answers and a one-column glyph does not.
Textual's own scrollbar shows proportion, never how many a filter removed.

**Why DataTable rather than ListView.** ListView mounts one widget per row, so
a filter keystroke over 95 tools destroyed and rebuilt ~190 widgets. Measured
on this checkout: 131ms per keystroke, 1715ms to switch section. DataTable is
virtualised -- it renders only the rows on screen -- and takes cells rather
than pre-formatted strings, which is what lets a column carry its own colour
instead of every row being one flat label. Same benchmark: 4.3x faster, and
the rebuild is debounced on top of that, so typing echoes immediately and the
table settles once you pause.

Subclasses declare COLUMNS and return CELLS, not strings. A cell may be a
plain str or a Rich Text, so severity, device state and staleness can each be
coloured where they are rather than in a format string nobody can restyle.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import DataTable, Input, Label

# Long enough that a fast typist rebuilds once instead of per key, short
# enough that a pause feels like the list is keeping up. Measured against a
# 95-row rebuild at ~25ms after the DataTable switch.
FILTER_DEBOUNCE = 0.12


class Catalogue(Vertical):
    BINDINGS = [
        Binding("slash", "filter", "filter", key_display="/"),
        Binding("escape", "clear_filter", "clear", show=False),
    ]

    snapshot = reactive(None)
    query = reactive("")

    empty_message = "nothing here yet"
    noun = "items"          # subclasses name what they list: "tools", "notes"...
    COLUMNS = ()            # ((label, width_or_None), ...)

    class Chosen(Message):
        def __init__(self, payload):
            self.payload = payload
            super().__init__()

    def __init__(self, **kw):
        super().__init__(**kw)
        self._payloads = []
        self._debounce = None

    def rows(self, snap):
        """[(cells, payload), ...] for this snapshot and self.query.

        `cells` is a tuple matching COLUMNS. Each entry is a str or a Rich
        Text -- Text is how a column carries colour of its own.
        """
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
        table = DataTable(id="rows", cursor_type="row", zebra_stripes=False)
        table.show_header = bool(self.COLUMNS)
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#rows", DataTable)
        for label, width in self.COLUMNS:
            table.add_column(label, width=width)
        self._repopulate()
        # `mount()` is asynchronous, so the caller's `widget.focus()` in
        # `_show()` (screens/main.py) races compose and loses: this container
        # itself is not focusable, so that call is a silent no-op. Doing it
        # here instead runs once compose has finished, and lands keyboard
        # focus on the table so arrow keys work immediately, without a tab.
        table.focus()

    def watch_snapshot(self, _old, _new) -> None:
        self._repopulate()

    def watch_query(self, _old, _new) -> None:
        self._repopulate()

    def _repopulate(self) -> None:
        """Rebuild the table from `rows()`.

        Synchronous, unlike its ListView predecessor: `DataTable.clear()`
        does not defer removal, so there is no un-awaited AwaitRemove to
        leave a previous population in the DOM. That bug rendered every list
        twice for seventeen reviews -- 95 tools as 190 rows, the count label
        saying 95 while showing 190, and Enter dead on every row past the
        first population because the index ran off the end of _payloads.
        """
        try:
            table = self.query_one("#rows", DataTable)
            count = self.query_one("#count", Label)
        except Exception:  # noqa: BLE001
            return                      # not mounted yet
        if not table.columns:
            # A watcher can fire before on_mount has added the columns --
            # MainScreen._show assigns .snapshot immediately after mount(),
            # and mount() is asynchronous. Adding rows to a column-less table
            # raises "More values provided than there are columns", which
            # showed up as an INTERMITTENT failure under load and passed
            # every time in isolation. on_mount repopulates once the columns
            # exist, so returning here loses nothing.
            return
        rows = self.visible_rows()
        self._payloads = [payload for _cells, payload in rows]
        count.update(self.count_label())
        table.clear()
        if not rows:
            table.add_row(*self._empty_cells())
            return
        for cells, _payload in rows:
            table.add_row(*cells)

    def _empty_cells(self):
        """The empty message, padded to the column count so DataTable takes it."""
        from rich.text import Text
        first = Text(self.empty_message, style="italic")
        return (first,) + ("",) * (max(1, len(self.COLUMNS)) - 1)

    # ------------------------------------------------------------ filtering --

    def action_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_clear_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.value = ""
        self.query = ""
        self.query_one("#rows", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Debounce the rebuild.

        The Input echoes the keystroke immediately either way; what used to
        lag was rebuilding the whole table between keys. Coalescing to one
        rebuild after a short pause is the difference between typing that
        stutters and typing that does not.
        """
        if event.input.id != "filter":
            return
        if self._debounce is not None:
            self._debounce.stop()
        value = event.value
        self._debounce = self.set_timer(
            FILTER_DEBOUNCE, lambda: self._apply_filter(value))

    def _apply_filter(self, value) -> None:
        self._debounce = None
        self.query = value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the filter moves to the table WITHOUT closing the filter.

        The old console's filter swallowed enter to leave filter mode, so
        opening a filtered result took two keystrokes that looked identical
        and only the second did anything. The query is left as it is: moving
        focus is not the same as clearing what was typed.

        Applies any pending debounce first, so Enter never races a rebuild
        that has not happened yet.
        """
        if event.input.id != "filter":
            return
        if self._debounce is not None:
            self._debounce.stop()
            self._apply_filter(event.value)
        self.query_one("#rows", DataTable).focus()

    def on_data_table_row_selected(self, event) -> None:
        index = event.cursor_row
        if index is not None and 0 <= index < len(self._payloads):
            self.post_message(self.Chosen(self._payloads[index]))
