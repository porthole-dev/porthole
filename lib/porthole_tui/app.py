# SPDX-License-Identifier: MIT
"""PortholeApp: the console.

A second front end, not a replacement. Every action here has a verb behind it,
because agents drive verbs and a capability reachable only by a human is one
agents cannot use.
"""
from __future__ import annotations

import pathlib

from textual.app import App

from . import jobs, state
from .screens.main import MainScreen

SECTIONS = [("port", "PORT"), ("devices", "DEVICES"), ("tools", "TOOLS"),
            ("brain", "BRAIN"), ("logs", "LOGS"), ("jobs", "JOBS")]


class PortholeApp(App):
    CSS_PATH = "styles/base.tcss"
    TITLE = "porthole"

    def __init__(self, root, device=None, **kw):
        super().__init__(**kw)
        self.root = pathlib.Path(root)
        self.store = state.Store(self.root, device)
        self.jobs = jobs.JobManager(self.root)

    @property
    def snapshot(self):
        return self.store.snapshot

    def on_mount(self) -> None:
        self.push_screen(MainScreen(SECTIONS))
