# SPDX-License-Identifier: MIT
"""A real file picker.

The console had no filesystem navigation of any kind, which is why a vendor
image could not be pointed at: the only way to name a path was to leave and
use the shell. Starting at PORTHOLE_WORKDIR rather than $PWD matters -- the
image you want is next to the port, not next to wherever the terminal happens
to be. That resolution lives in form.py (it needs the warm Store, which this
screen deliberately does not depend on); this screen just renders whatever
directory it is told to start at.
"""
from __future__ import annotations

import pathlib

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Input, Label


class _FilteredTree(DirectoryTree):
    """Every directory, but files only if they match `extensions`.

    A picker with `extensions=()` shows everything -- most verbs take a path
    that could be a zip, an image, a directory, or a device node, and second-
    guessing that from the CLI's own metavar would refuse names the verb
    would happily accept.
    """

    def __init__(self, path, extensions=(), **kw):
        super().__init__(path, **kw)
        self._extensions = tuple(
            e if e.startswith(".") else "." + e for e in extensions)

    def filter_paths(self, paths):
        if not self._extensions:
            return paths
        return [p for p in paths
                if p.is_dir() or p.suffix.lower() in self._extensions]


class FilePicker(ModalScreen):
    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+s", "choose", "choose"),
    ]

    def __init__(self, start=None, extensions=(), **kw):
        super().__init__(**kw)
        start = pathlib.Path(start or pathlib.Path.cwd()).expanduser()
        if not start.is_dir():
            start = start.parent if start.parent.is_dir() else pathlib.Path.cwd()
        self.start = start
        self.extensions = tuple(extensions)
        self._chosen = None

    def compose(self) -> ComposeResult:
        yield Label("choose a file -- {}".format(self.start), id="reader-title")
        yield _FilteredTree(str(self.start), self.extensions, id="picker-tree")
        yield Input(placeholder="...or type a path", id="picker-path")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self._chosen = str(event.path)
        self.query_one("#picker-path", Input).value = self._chosen
        self.dismiss(self._chosen)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or self._chosen or None)

    def action_choose(self) -> None:
        typed = self.query_one("#picker-path", Input).value.strip()
        self.dismiss(typed or self._chosen or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
