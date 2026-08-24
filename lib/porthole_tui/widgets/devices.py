# SPDX-License-Identifier: MIT
"""Devices: which ports exist, which is live, and what each is wired to.

This exists because of a real bug: the working repo and the pmaports checkout
used to be global, so switching devices left you in the previous device's
files. Showing device and workdir on one line makes that class of mistake
visible instead of silent.

Only the live device's workdir is shown, read off the warm snapshot -- not
re-read from another device's profile on disk, which would be exactly the
per-frame filesystem re-read the tools pane's docstring warns about, just for
a shorter list.
"""
from __future__ import annotations

from .catalogue import Catalogue


class DeviceList(Catalogue):
    noun = "devices"
    empty_message = "no device profiles yet — try `porthole new-device`"

    def rows(self, snap):
        devices = list((snap.devices if snap else None) or [])
        query = (self.query or "").lower()
        if query:
            devices = [d for d in devices if query in d.lower()]
        out = []
        for name in devices:
            live = bool(snap and name == snap.device)
            workdir = (snap.workdir if live and snap else "") or ""
            out.append(("{} {:<20} {}".format(
                "*" if live else " ", name[:20],
                workdir or ("not set" if live else "-")), name))
        return out
