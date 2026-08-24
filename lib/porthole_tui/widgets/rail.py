# SPDX-License-Identifier: MIT
"""The rail: the phase spine, and how to get anywhere.

The spine is the console's signature and it is honest -- bring-up genuinely is
a sequence, so ordering carries information rather than decorating. It is
docked and never scrolls, because "how far along am I" must be answerable
without moving.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Label, Static

from ..state import phases


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
            yield Label("  no milestones yet", classes="empty rail--phase")
        for index, phase in enumerate(phases(rows)):
            classes = ("rail--phase -current" if phase["current"]
                       else "rail--phase")
            yield Label("{} {:<11} {}/{}".format(
                index, phase["name"][:11], phase["done"], phase["total"]),
                classes=classes)
        yield Label("", classes="rail--phase")
        for key, title in self.sections:
            yield Label("{:<9}{}".format(title, self._count(key)),
                        classes=("rail--section -active" if key == self.section
                                 else "rail--section"))

    def _count(self, key):
        snap = self.snapshot
        if snap is None:
            return ""
        return {"tools": len(snap.tools or []),
                "brain": len(snap.notes or []),
                "devices": len(snap.devices or [])}.get(key, "")
