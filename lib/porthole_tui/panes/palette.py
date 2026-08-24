# SPDX-License-Identifier: MIT
"""The command palette: everything reachable in two keystrokes.

This is what makes a TUI faster than the CLI rather than merely present.
Without it, a full-screen app over a tool whose commands were already one
keystroke away is a slower menu.

It searches across all four namespaces at once -- verbs, tools, notes,
milestones -- because a porter does not think "is what I want a verb or a
tool", they think "suspend" or "offsets" and want whatever matches.
"""
from __future__ import annotations

from .. import theme as T

KIND_ROLE = {"verb": T.NOTE, "tool": T.OK, "note": T.WARN,
             "milestone": T.ACTIVE}


def collect(snap) -> list[dict]:
    """Every addressable thing, flattened once per refresh."""
    import pathlib
    root = pathlib.Path(snap.cfg.get("PORTHOLE_ROOT", "."))
    items: list[dict] = []

    try:
        import porthole_cli
        for spec in porthole_cli.discover(root):
            items.append({"kind": "verb", "id": spec["verb"],
                          "label": spec["verb"], "detail": spec["help"],
                          "run": f"porthole {spec['verb']}"})
    except Exception:  # noqa: BLE001
        pass

    try:
        import porthole_cmd_tools as tmod
        for tool in tmod.collect(root, snap.device):
            items.append({"kind": "tool", "id": tool.name, "label": tool.name,
                          "detail": tool.summary or "",
                          "run": f"porthole run {tool.name}"})
    except Exception:  # noqa: BLE001
        pass

    try:
        import porthole_cmd_brain as bmod
        for note in bmod.load_notes(root):
            items.append({"kind": "note", "id": note.id, "label": note.id,
                          "detail": note.title,
                          "run": f"porthole brain search {note.id}"})
    except Exception:  # noqa: BLE001
        pass

    for row in (snap.rows or []):
        items.append({"kind": "milestone", "id": row["id"],
                      "label": row["id"], "detail": row["title"],
                      "run": row["how"]})
    return items


def score(item: dict, query: str) -> int:
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


def rank(items, query, limit=40):
    scored = [(score(i, query), i) for i in items]
    return [i for s, i in sorted(
        (p for p in scored if p[0] > 0),
        key=lambda p: (-p[0], p[1]["label"]))][:limit]


def render(win, snap, height, width, sel=0, query="", items=None):
    hits = rank(items if items is not None else collect(snap), query)
    win.addstr(1, 2, "/", T.attr(T.NOTE, bold=True))
    win.addstr(1, 4, query or "type to search verbs, tools, notes, milestones",
               T.attr(T.BASE if query else T.DIM))
    for i, item in enumerate(hits):
        y = 3 + i
        if y >= height - 2:
            break
        win.addstr(y, 2, T.fit(item["kind"], 9).ljust(9),
                   T.attr(KIND_ROLE.get(item["kind"], T.DIM)))
        win.addstr(y, 12, T.fit(item["label"], 28).ljust(28),
                   T.attr(T.BASE, reverse=(i == sel)))
        win.addstr(y, 42, T.fit(item["detail"], max(0, width - 44)),
                   T.attr(T.DIM))
    if not hits:
        win.addstr(3, 2, "nothing matches", T.attr(T.WARN))
    return len(hits)


def keys():
    return [("enter", "run it"), ("esc", "close")]
