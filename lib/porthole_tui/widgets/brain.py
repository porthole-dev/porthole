# SPDX-License-Identifier: MIT
"""Brain: the notes, scoped to what you are actually working on.

Filtering always KEEPS the laws. Hiding them from someone who filtered to their
SoC would be exactly backwards -- the laws are the notes that stop you wasting
a week, and they apply to every device.
"""
from __future__ import annotations

from .catalogue import Catalogue


class NoteList(Catalogue):
    noun = "notes"
    empty_message = "no note matches that filter"

    def rows(self, snap):
        notes = list((snap.notes if snap else None) or [])
        query = (self.query or "").lower()
        if query:
            notes = [n for n in notes
                     if query in n.title.lower() or query in n.id.lower()
                     or query in n.body.lower()
                     or n.meta.get("severity") == "law"]
        return [("{:<9} {}".format(n.meta.get("severity", "fact")[:9],
                                   n.title[:70]), n) for n in notes]
