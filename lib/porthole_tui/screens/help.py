# SPDX-License-Identifier: MIT
"""The key reference, generated from the live BINDINGS.

Generated rather than written, because the curses console's help, footer and
key handlers disagreed: `/` was documented as "filter" in two panes while it
actually opened the palette, and the real filter key was in no footer at all.
A help screen that reads the bindings cannot drift from them.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class HelpScreen(ModalScreen):
    # action_close is defined on this class, so this resolves against the
    # Screen namespace directly -- ruling R20.
    BINDINGS = [Binding("escape,q,question_mark", "close", "back")]

    def compose(self) -> ComposeResult:
        yield Label("keys", id="reader-title")
        with VerticalScroll():
            for title, bindings in self._sources():
                lines = ["  {:<16} {}".format(b.key_display or b.key,
                                              b.description)
                         for b in bindings if b.description]
                if lines:
                    yield Static("{}\n{}".format(title, "\n".join(lines)))
            yield Static(
                "\nAnything that flashes, writes to the device or touches a\n"
                "slot needs an explicit confirmation naming the command.")

    def _sources(self):
        """Grouped from the screen below's active_bindings.

        `below.query("*")` was wrong twice over. It walked every widget's
        Textual-internal bindings, so the reference grew sections for Input,
        ListView, RichLog and Footer and gave `up` four different meanings --
        and two of those sections were false, because neither Footer nor
        RichLog is focusable in that state and neither binding could ever
        fire. active_bindings is public API, it is exactly what the footer
        computes, and it resolves the real focus chain instead of guessing at
        it, so help and footer cannot disagree. It also removes the last
        private-attribute access in this file.
        """
        stack = self.app.screen_stack
        below = stack[-2] if len(stack) > 1 else None
        if below is None:
            return []
        groups, order = {}, []
        for active in below.active_bindings.values():
            name = type(active.node).__name__
            if name not in groups:
                groups[name] = []
                order.append(name)
            groups[name].append(active.binding)
        return [(name, groups[name]) for name in order]

    def action_close(self) -> None:
        self.dismiss(None)
