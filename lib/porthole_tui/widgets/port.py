# SPDX-License-Identifier: MIT
"""The port view: what is wrong, and the one next thing.

Stale first, always. A milestone ticked that the tool can see is false is the
one thing that must never scroll off: it means the port believes it is further
along than it is, and that is the direction that gets someone flashing.
"""
from __future__ import annotations

from .catalogue import Catalogue


class PortView(Catalogue):
    noun = "milestones"
    empty_message = "no device selected — press 2 to pick one"

    def rows(self, snap):
        import porthole_milestones as ms
        if not snap or not snap.device:
            return []
        all_rows = list(snap.rows or [])
        stale = [r for r in all_rows if r["source"] == "stale"]
        blocked = [r for r in all_rows
                   if r["state"] == ms.BLOCKED and r["source"] != "stale"]
        ordered = stale + blocked
        ordered += [r for r in all_rows if r not in ordered]
        query = (self.query or "").lower()
        if query:
            # After the stale/blocked ordering, not before: a stale milestone
            # matching the filter must still lead within the filtered set --
            # it means the port believes it is further along than it is, and
            # that is the one thing that must never scroll off.
            ordered = [r for r in ordered
                      if query in (r["title"] or "").lower()
                      or query in (r["id"] or "").lower()
                      or query in (r["evidence"] or "").lower()
                      or query in (r["phase"] or "").lower()]
        out = []
        for row in ordered:
            if row["source"] == "stale":
                tag = "! stale "
            elif row["state"] == ms.BLOCKED:
                tag = "- blocked"
            else:
                tag = "         "
            out.append(("{} {:<38} {}".format(
                tag, row["title"][:38], (row["evidence"] or "")[:40]), row))
        return out

    def next_step(self):
        summary = (self.snapshot.summary if self.snapshot else None) or {}
        return summary.get("next")
