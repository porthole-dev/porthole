# SPDX-License-Identifier: MIT
"""Full screen, exact command, explicit key. No y/n muscle memory.

A mis-keystroke that flashes a device is not recoverable by pressing undo, so
this deliberately has no default button, does not accept enter, and does not
put the destructive option under the cursor. There is no BINDINGS list here on
purpose: on_key below intercepts every key itself, so there is no Binding to
get the R20 resolution wrong -- y dismisses True, anything else (including
enter and escape) dismisses False.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class ConfirmRun(ModalScreen):
    def __init__(self, command, risky, device="", **kw):
        super().__init__(**kw)
        self.command, self.risky = command, risky
        self.device = device

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Label("This can change the device" if self.risky
                        else "Run this?",
                        classes="error" if self.risky else "")
            yield Static(self.command, id="confirm-command")
            # The command reproduces exactly and still does not name the
            # phone: `porthole flash boot --slot b` is the same string for
            # every device you own.
            if self.device:
                yield Label("on device: {}".format(self.device),
                            classes="error" if self.risky else "empty")
            if self.risky:
                yield Label("A bad image on the wrong slot can leave this "
                            "device unbootable.", classes="error")
                yield Label("Check the slot policy before you agree.",
                            classes="error")
            yield Label("press y to run, any other key to cancel",
                        classes="empty")

    def on_key(self, event) -> None:
        event.stop()
        self.dismiss(event.key == "y")
