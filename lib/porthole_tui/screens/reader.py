# SPDX-License-Identifier: MIT
"""A scrollable reader for a content.Doc.

One pager serves tools, notes and milestones, because they are all the same
shape: a title and some lines you scroll. What differs is who builds the lines.

`x` runs it, not `r`. `r` is refresh everywhere else in this app, and the
curses console bound both to `r`: the refresh branch matched first and the
advertised "run it" was dead code for the life of the program. Two meanings for
one key is what caused that.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class Reader(ModalScreen):
    # Both actions below are defined on THIS class (action_close, action_run),
    # so they resolve against the Screen namespace with no app. prefix needed
    # -- ruling R20. Neither name collides with a Screen default.
    BINDINGS = [
        Binding("escape,q", "close", "back"),
        Binding("x", "run", "run it"),
    ]

    def __init__(self, doc, **kw):
        super().__init__(**kw)
        self.doc = doc

    def compose(self) -> ComposeResult:
        yield Label(self.doc.title, id="reader-title")
        with VerticalScroll(id="reader-body"):
            yield Static("\n".join(self.doc.lines))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_run(self) -> None:
        self.dismiss(self.doc.command or None)
