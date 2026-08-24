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
        out = [("Global", self._of(self.app))]
        stack = self.app.screen_stack
        below = stack[-2] if len(stack) > 1 else None
        if below is not None:
            out.append((type(below).__name__, self._of(below)))
            for widget in below.query("*"):
                bindings = self._of(widget)
                if bindings:
                    out.append((type(widget).__name__, bindings))
        return out

    @staticmethod
    def _of(node):
        holder = getattr(node, "_bindings", None)
        if holder is None:
            return []
        try:
            # Older textual: BindingsMap.keys is a {key: Binding} dict.
            return list(holder.keys.values())
        except AttributeError:
            pass
        # textual 8.2.8: no .keys attribute. key_to_bindings is a
        # {key: [Binding, ...]} dict instead -- confirmed on the installed
        # version, not guessed. Flatten it; a multi-key Binding ("escape,q")
        # already comes back as one distinct Binding object per key, so no
        # dedup is needed.
        try:
            out = []
            for bindings in holder.key_to_bindings.values():
                out.extend(bindings)
            return out
        except AttributeError:
            return []

    def action_close(self) -> None:
        self.dismiss(None)
