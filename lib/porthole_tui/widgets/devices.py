# SPDX-License-Identifier: MIT
"""Devices: which ports exist, which is live, and what each is wired to.

This exists because of a real bug: the working repo and the pmaports checkout
used to be global, so switching devices left you in the previous device's
files. Showing workdir AND pmaports on one line per device -- for EVERY
device, not just the active one -- makes that class of mistake visible
instead of silent. A row shown as "not set" is not inherited from another
device; it genuinely has no override.

Every device's paths are read straight off `snap.device_paths`, resolved once
per refresh in the warm model (state.py), never re-read from disk here: that
would be exactly the per-frame filesystem cost the tools pane's docstring
warns about, just for a shorter list.
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
            paths = (snap.device_paths or {}).get(name, {}) if snap else {}
            workdir = paths.get("workdir") or "not set"
            pmaports = paths.get("pmaports") or "shared"
            out.append(("{} {:<18} {:<34} {}".format(
                "*" if live else " ", name[:18], workdir[-34:],
                pmaports[-24:]), name))
        return out
