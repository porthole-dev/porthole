# SPDX-License-Identifier: MIT
"""The rail: the phase spine, and how to get anywhere.

The spine is the console's signature and it is honest -- bring-up genuinely is
a sequence, so ordering carries information rather than decorating. It is
docked and never scrolls, because "how far along am I" must be answerable
without moving.

Each phase carries a small fill bar for the same reason the old console's
`theme.bar()` did: eight cells is enough to see movement at a glance and too
few to become a dashboard. A port's progress is a fact, not a metric to
optimise. The bar is redundant with the `3/9` beside it on purpose -- colour
and glyph both, so the rail still reads on a terminal with neither.

A phase that carries a stale milestone is marked. Stale means the port
believes it is further along than it is, and the rail is where that should be
visible before you go looking for it.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Label, Static

from .. import ink
from ..state import phases

BAR = 8


def fill(done, total, width=BAR):
    """A fill bar. Solid for what is done, light for what is not."""
    if total <= 0:
        return "░" * width
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled)


class Rail(Static):
    # Focusable so tab can land on the rail itself: it has no interactive
    # children, and the focus border (`:focus-within`, base.tcss) needs
    # somewhere to attach.
    can_focus = True

    snapshot = reactive(None, recompose=True)
    section = reactive("port", recompose=True)

    def __init__(self, sections, **kw):
        super().__init__(**kw)
        self.sections = sections

    def compose(self) -> ComposeResult:
        snap = self.snapshot
        yield Label("PORT", classes="rail--heading")
        rows = (snap.rows if snap else None) or []
        if not rows:
            yield Label("no milestones yet", classes="empty rail--phase")
        for index, phase in enumerate(phases(rows)):
            yield Label(self._phase_line(index, phase),
                        classes=("rail--phase -current" if phase["current"]
                                 else "rail--phase"))
        yield Label("", classes="rail--phase")
        for key, title in self.sections:
            yield Label(self._section_line(key, title),
                        classes=("rail--section -active" if key == self.section
                                 else "rail--section"))

    def _phase_line(self, index, phase):
        done, total = phase["done"], phase["total"]
        complete = total and done == total
        line = Text()
        line.append("{} ".format(index), style=ink.DIM)
        line.append("{:<11}".format(phase["name"][:10]),
                    style=ink.ACTIVE if phase["current"] else ink.BASE)
        line.append(fill(done, total),
                    style=ink.OK if complete else
                    (ink.ACTIVE if phase["current"] else ink.DIM))
        # A stale milestone in this phase outranks the count: the port
        # believes it is further along than it is.
        if phase.get("stale"):
            line.append(" !{}".format(phase["stale"]), style=ink.CRIT)
        else:
            line.append(" {}/{}".format(done, total), style=ink.DIM)
        return line

    def _section_line(self, key, title):
        count = self._count(key)
        line = Text()
        line.append("{:<9}".format(title),
                    style=ink.ACTIVE if key == self.section else ink.BASE)
        line.append("" if count == "" else str(count), style=ink.DIM)
        return line

    def _count(self, key):
        snap = self.snapshot
        if snap is None:
            return ""
        return {"tools": len(snap.tools or []),
                "brain": len(snap.notes or []),
                "devices": len(snap.devices or [])}.get(key, "")
