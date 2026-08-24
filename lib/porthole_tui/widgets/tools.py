# SPDX-License-Identifier: MIT
"""Tools: 98 of them, which is more than anyone holds in their head.

Read from the warm snapshot, never the filesystem: re-parsing 95 tool headers
on every frame cost 14.3% of a core while the old pane sat idle.
"""
from __future__ import annotations

from .catalogue import Catalogue


class ToolList(Catalogue):
    noun = "tools"
    empty_message = "no tool matches that filter"

    def rows(self, snap):
        tools = list((snap.tools if snap else None) or [])
        query = (self.query or "").lower()
        if query:
            tools = [t for t in tools if query in t.name.lower()
                     or query in (t.summary or "").lower()]
        out = []
        for tool in tools:
            needs = (getattr(tool, "needs", "") or "-").upper()
            out.append(("{:<28} {:<9} {}".format(
                tool.name[:28], needs[:9], (tool.summary or "")[:60]), tool))
        return out
