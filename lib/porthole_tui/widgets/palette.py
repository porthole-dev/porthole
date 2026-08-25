# SPDX-License-Identifier: MIT
"""The command palette: everything reachable in two keystrokes.

Ranking is ported unchanged and is deliberately not fuzzy-with-typos. A porter
typing `susp` wants suspend, but a tool that guesses too eagerly offers to
flash something when you meant to read a note. Exactness is cheap here and the
failure is expensive.

What changed is what happens on Enter. The old palette built `porthole <verb>`
for every verb and ran it, so it reached a verb's default and nothing past it.
An item that takes arguments now opens its form instead.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label

from .. import argspec, ink
from .catalogue import FILTER_DEBOUNCE

# The badge each namespace wears. A nine-character lowercase word in one of
# four foreground colours was the only signal separating a verb from a tool,
# and on an eight-colour terminal two of the four collapsed.
BADGE = {"verb": "VERB", "tool": "TOOL", "note": "NOTE", "milestone": "STEP"}


def score(item, query):
    """Subsequence match, ranked. Higher is better; 0 means no match.

    Deliberately not fuzzy-with-typos: a porter typing `susp` wants suspend,
    but a tool that guesses too eagerly offers to flash something when you
    meant to read a note. Exactness is cheap here and the failure is expensive.
    """
    if not query:
        return 1
    q, label, detail = query.lower(), item["label"].lower(), item["detail"].lower()
    if label == q:
        return 1000
    if label.startswith(q):
        return 500 - len(label)
    if q in label:
        return 300 - len(label)
    if q in detail:
        return 100
    # subsequence, e.g. "sspc" -> "suspend-cycle"
    i = 0
    for ch in label:
        if i < len(q) and ch == q[i]:
            i += 1
    return 50 if i == len(q) else 0


def rank(items, query, limit=500):
    scored = [(score(i, query), i) for i in items]
    return [i for s, i in sorted(
        (p for p in scored if p[0] > 0),
        key=lambda p: (-p[0], p[1]["label"]))][:limit]


def collect(root, snap) -> list:
    """Every addressable thing, flattened once per refresh."""
    import pathlib
    root = pathlib.Path(root)
    items = []
    try:
        import porthole_cli
        for spec in porthole_cli.discover(root):
            items.append({"kind": "verb", "id": spec["verb"],
                          "label": spec["verb"], "detail": spec["help"],
                          "run": "porthole " + spec["verb"], "spec": spec,
                          "needs_args": argspec.needs_arguments(spec)})
    except Exception:  # noqa: BLE001
        pass
    device = getattr(snap, "device", "") if snap is not None else ""
    try:
        import porthole_cmd_tools as tmod
        for tool in tmod.collect(root, device):
            items.append({"kind": "tool", "id": tool.name, "label": tool.name,
                          "detail": tool.summary or "",
                          "run": "porthole run " + tool.name,
                          "spec": None, "needs_args": False})
    except Exception:  # noqa: BLE001
        pass
    try:
        import porthole_cmd_brain as bmod
        for note in bmod.load_notes(root):
            items.append({"kind": "note", "id": note.id, "label": note.id,
                          "detail": note.title,
                          "run": "porthole brain search " + note.id,
                          "spec": None, "needs_args": False})
    except Exception:  # noqa: BLE001
        pass
    for row in (getattr(snap, "rows", None) or []):
        items.append({"kind": "milestone", "id": row["id"], "label": row["id"],
                      "detail": row["title"], "run": row["how"],
                      "spec": None, "needs_args": False, "row": row})
    return items


class Palette(ModalScreen):
    """Everything addressable, in two keystrokes.

    A DataTable rather than a ListView for the same reason the catalogues
    changed: ListView mounts a widget per row, and this list is ~180 items
    re-ranked on every keystroke. It also gives the badge a column of its
    own, so a verb and a tool are told apart by a reverse-video chip instead
    of a nine-character lowercase word in one of four colours -- two of which
    collapsed into each other on an eight-colour terminal.
    """

    BINDINGS = [Binding("escape", "cancel", "close")]

    def __init__(self, items, **kw):
        super().__init__(**kw)
        self.items = items
        self.hits = []
        self._debounce = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="search verbs, tools, notes, milestones",
                    id="palette-query")
        table = DataTable(id="palette-rows", cursor_type="row",
                          show_header=False)
        yield table
        # A modal fills the screen, so MainScreen's own footer is covered
        # while this is up. Without one here the keys below are advertised
        # nowhere at all -- and no modal binds `?`, so help cannot be
        # reached from one either.
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#palette-rows", DataTable)
        table.add_column("", width=6)
        table.add_column("", width=30)
        table.add_column("", width=None)
        self._repopulate("")
        self.query_one("#palette-query", Input).focus()

    def _repopulate(self, query) -> None:
        """Synchronous: DataTable.clear() does not defer removal, so there is
        no un-awaited AwaitRemove to leave the previous result set stacked
        under the new one."""
        table = self.query_one("#palette-rows", DataTable)
        if not table.columns:
            return                      # see Catalogue._repopulate
        self.hits = rank(self.items, query)
        table.clear()
        if not self.hits:
            table.add_row(ink.ink("nothing matches", ink.DIM), "", "")
            return
        for item in self.hits[:200]:
            table.add_row(
                ink.badge(item["kind"]),
                ink.ink(item["label"], ink.BASE, 30),
                ink.ink(item["detail"], ink.DIM, 60))

    def on_input_changed(self, event) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        value = event.value
        self._debounce = self.set_timer(
            FILTER_DEBOUNCE, lambda: self._apply(value))

    def _apply(self, value) -> None:
        self._debounce = None
        self._repopulate(value)

    def on_input_submitted(self, event) -> None:
        if self._debounce is not None:
            self._debounce.stop()
            self._apply(event.value)
        self.query_one("#palette-rows", DataTable).focus()

    def on_data_table_row_selected(self, event) -> None:
        index = event.cursor_row
        if index is not None and 0 <= index < len(self.hits):
            self.dismiss(self.hits[index])

    def action_cancel(self) -> None:
        self.dismiss(None)
