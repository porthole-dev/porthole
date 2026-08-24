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
from textual.widgets import Input, Label, ListItem, ListView

from .. import argspec

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
    BINDINGS = [Binding("escape", "cancel", "close")]

    def __init__(self, items, **kw):
        super().__init__(**kw)
        self.items = items
        self.hits = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="search verbs, tools, notes, milestones",
                    id="palette-query")
        yield ListView(id="palette-rows")

    async def on_mount(self) -> None:
        self.query_one("#palette-query", Input).focus()
        await self._repopulate("")

    async def _repopulate(self, query) -> None:
        # Awaited for the same reason Catalogue._repopulate is: clear()
        # returns an AwaitRemove and the rows are still there when it
        # returns, so an un-awaited clear leaves the previous result set
        # stacked under the new one -- and on_list_view_selected's
        # `index < len(self.hits)` guard then makes Enter dead on the half
        # that has no hit behind it.
        listing = self.query_one("#palette-rows", ListView)
        await listing.clear()
        self.hits = rank(self.items, query)
        if not self.hits:
            listing.append(ListItem(Label("nothing matches", classes="empty")))
            return
        for item in self.hits[:200]:
            listing.append(ListItem(Label("{:<5} {:<28} {}".format(
                BADGE.get(item["kind"], "?"), item["label"][:28],
                item["detail"][:50]))))

    async def on_input_changed(self, event) -> None:
        await self._repopulate(event.value)

    def on_input_submitted(self, event) -> None:
        self.query_one("#palette-rows", ListView).focus()

    def on_list_view_selected(self, event) -> None:
        index = event.list_view.index
        if index is not None and index < len(self.hits):
            self.dismiss(self.hits[index])

    def action_cancel(self) -> None:
        self.dismiss(None)
