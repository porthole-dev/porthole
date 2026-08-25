# SPDX-License-Identifier: MIT
"""Brain: the notes, scoped to what you are actually working on.

Filtering always KEEPS the laws. Hiding them from someone who filtered to
their SoC would be exactly backwards -- the laws are the notes that stop you
wasting a week, and they apply to every device.

Severity carries the colour because it carries the meaning: a law you skim
past is the expensive kind of mistake, and it should not look like a fact.
"""
from __future__ import annotations

from .. import ink
from .catalogue import Catalogue


class NoteList(Catalogue):
    noun = "notes"
    empty_message = "no note matches that filter"
    COLUMNS = (("severity", 10), ("note", None))

    def rows(self, snap):
        notes = list((snap.notes if snap else None) or [])
        query = (self.query or "").lower()
        if query:
            notes = [n for n in notes
                     if query in n.title.lower() or query in n.id.lower()
                     or query in n.body.lower()
                     or n.meta.get("severity") == "law"]
        return [((ink.severity(n.meta.get("severity")),
                  ink.ink(n.title, ink.BASE, 72)), n) for n in notes]
