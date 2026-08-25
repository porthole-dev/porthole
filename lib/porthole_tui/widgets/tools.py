# SPDX-License-Identifier: MIT
"""Tools: 95 of them, which is more than anyone holds in their head.

Read from the warm snapshot, never the filesystem: re-parsing 95 tool headers
on every frame cost 14.3% of a core while the old pane sat idle.

`needs` is the useful axis, not the name. "What can I run right now" is the
question, and running a BOOTED tool at a device in fastboot is a wasted cycle
and a confusing error -- so the column is coloured by whether it is free to
run rather than left as one more grey word.
"""
from __future__ import annotations

from .. import ink
from .catalogue import Catalogue


class ToolList(Catalogue):
    noun = "tools"
    empty_message = "no tool matches that filter"
    COLUMNS = (("tool", 30), ("needs", 9), ("what it does", None))

    def rows(self, snap):
        tools = list((snap.tools if snap else None) or [])
        query = (self.query or "").lower()
        if query:
            tools = [t for t in tools if query in t.name.lower()
                     or query in (t.summary or "").lower()]
        return [((ink.ink(t.name, ink.BASE, 30),
                  ink.needs(getattr(t, "needs", "")),
                  ink.ink(t.summary or "", ink.DIM, 62)), t)
                for t in tools]
