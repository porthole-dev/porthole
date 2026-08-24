# SPDX-License-Identifier: MIT
"""Brain: the notes, scoped to what you are actually working on.

Scope filtering always INCLUDES the generic notes. Hiding the laws from someone
who filtered to their SoC would be exactly backwards -- the laws are the notes
that stop you wasting a week, and they apply to every device.
"""
from __future__ import annotations

from .. import theme as T
from . import _list

SEV = {"law": T.CRIT, "trap": T.WARN, "technique": T.NOTE, "fact": T.DIM}


def render(win, snap, height, width, sel=0, query=""):
    notes = list(snap.notes or [])
    if query:
        q = query.lower()
        notes = [n for n in notes
                 if q in n.title.lower() or q in n.id.lower()
                 or q in n.body.lower()]
    rows = max(1, height - 5)
    visible, offset, sel = _list.window(notes, sel, rows)
    count = _list.scrollbar(offset, len(visible), len(notes))
    win.addstr(1, 2, f"{len(notes)} note(s)" + (f"  filter: {query}" if query
                                                else ""), T.attr(T.DIM))
    if count:
        win.addstr(1, max(0, width - len(count) - 2), count, T.attr(T.DIM))
    for i, note in enumerate(visible):
        y = 3 + i
        sev = note.meta.get("severity", "fact")
        win.addstr(y, 2, T.fit(sev, 9).ljust(9), T.attr(SEV.get(sev, T.DIM)))
        win.addstr(y, 12, T.fit(note.title, max(0, width - 14)),
                   T.attr(T.BASE, reverse=(offset + i == sel)))
    return len(notes)


def keys():
    return [("enter", "read it"), ("/", "filter")]
