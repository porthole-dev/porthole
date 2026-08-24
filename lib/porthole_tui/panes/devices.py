# SPDX-License-Identifier: MIT
"""Devices: which ports exist, which is live, and what each one is wired to.

This pane exists because of a real bug: the working repo and the pmaports
checkout used to be global, so switching devices left you in the previous
device's files. Showing all three -- device, repo, aports branch -- on one line
per device makes that class of mistake visible instead of silent.
"""
from __future__ import annotations

from .. import theme as T
from . import _list


def render(win, snap, height, width, sel=0):
    import porthole
    devices = snap.devices or []
    win.addstr(1, 2, "device", T.attr(T.DIM))
    win.addstr(1, 22, "working repo", T.attr(T.DIM))
    win.addstr(1, 58, "pmaports", T.attr(T.DIM))
    rows = max(1, height - 6)
    visible, offset, sel = _list.window(devices, sel, rows)
    count = _list.scrollbar(offset, len(visible), len(devices))
    if count:
        win.addstr(1, max(0, width - len(count) - 2), count, T.attr(T.DIM))
    for i, name in enumerate(visible):
        y = 3 + i
        live = name == snap.device
        cur = offset + i == sel
        mark = T.g("tick") if live else " "
        win.addstr(y, 2, f"{mark} {T.fit(name, 17).ljust(17)}",
                   T.attr(T.ACTIVE if live else T.BASE,
                          bold=live, reverse=cur))
        wd = _peek(snap, name, "PORTHOLE_WORKDIR")
        pm = _peek(snap, name, "PORTHOLE_PMAPORTS")
        win.addstr(y, 22, T.fit(wd or "not set", 34).ljust(34),
                   T.attr(T.BASE if wd else T.WARN))
        win.addstr(y, 58, T.fit(_short(pm) or "shared", max(0, width - 60)),
                   T.attr(T.BASE if pm else T.DIM))
    y = min(3 + len(visible), height - 3) + 1
    win.addstr(y, 2, T.fit("A repo shown as 'not set' is not inherited from "
                           "another device.", max(0, width - 4)), T.attr(T.DIM))
    return len(devices)


def _peek(snap, device, key):
    """Resolve one key for a device that is not the active one."""
    import porthole
    try:
        import pathlib
        cfg = porthole.load_config(
            root=pathlib.Path(snap.cfg.get("PORTHOLE_ROOT", ".")),
                                   env={"PORTHOLE_DEVICE": device})
        return cfg.get(key, "")
    except Exception:  # noqa: BLE001
        return ""


def _short(path):
    import pathlib
    if not path:
        return ""
    try:
        return "~/" + str(pathlib.Path(path).relative_to(pathlib.Path.home()))
    except ValueError:
        return str(path)


def keys():
    return [("enter", "switch to this device"), ("↑↓", "move")]
