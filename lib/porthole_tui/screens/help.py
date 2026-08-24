# SPDX-License-Identifier: MIT
"""Placeholder; the generated key reference lands in task 12."""
from __future__ import annotations

from textual.screen import ModalScreen
from textual.widgets import Label


class HelpScreen(ModalScreen):
    def compose(self):
        yield Label("keys arrive in task 12")

    def on_key(self, event):
        self.dismiss(None)
