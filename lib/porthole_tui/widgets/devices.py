# SPDX-License-Identifier: MIT
"""Devices: which ports exist, which is live, and what each is wired to.

This pane exists because of a real bug: the working repo and the pmaports
checkout used to be global, so switching devices left you in the previous
device's files. Showing each device's OWN paths on its own row makes that
class of mistake visible instead of silent -- which is why a row must never
render a dash for a device that is merely not the active one.

Paths are shortened relative to home rather than sliced from the left. A
mid-path cut like `le/aports/google-cheetah` reads as corruption.
"""
from __future__ import annotations

import pathlib

from .. import ink
from .catalogue import Catalogue


def short(path):
    """`~/rest`, so a long path stays recognisable at the front."""
    if not path:
        return ""
    try:
        return "~/" + str(pathlib.Path(path).relative_to(pathlib.Path.home()))
    except ValueError:
        return str(path)


class DeviceList(Catalogue):
    noun = "devices"
    empty_message = "no device profiles yet — try `porthole new-device`"
    COLUMNS = (("", 2), ("device", 20), ("working repo", 34), ("pmaports", None))

    def rows(self, snap):
        devices = list((snap.devices if snap else None) or [])
        query = (self.query or "").lower()
        if query:
            devices = [d for d in devices if query in d.lower()]
        out = []
        for name in devices:
            live = bool(snap and name == snap.device)
            paths = (snap.device_paths or {}).get(name, {}) if snap else {}
            out.append((
                (ink.ink("●" if live else "", ink.ACTIVE, 2),
                 ink.ink(name, ink.ACTIVE if live else ink.BASE, 20),
                 ink.ink(short(paths.get("workdir")) or "not set",
                         ink.BASE if paths.get("workdir") else ink.WARN, 34),
                 ink.ink(short(paths.get("pmaports")) or "shared", ink.DIM, 30)),
                name))
        return out
