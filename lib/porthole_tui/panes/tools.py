# SPDX-License-Identifier: MIT
"""Tools: 114 of them, which is more than anyone holds in their head.

Filtering by the device STATE a tool needs is the useful axis, not the name --
"what can I run right now" is the question, and running a BOOTED tool at a
device in fastboot is a wasted cycle and a confusing error.
"""
from __future__ import annotations

from .. import theme as T
from . import _list


def render(win, snap, height, width, sel=0, query=""):
    import pathlib
    import porthole_cmd_tools as tmod
    # Path, not str: collect() builds `root / "tools"`, and passing the config
    # value straight through crashed the pane the moment it was opened.
    root = pathlib.Path(snap.cfg.get("PORTHOLE_ROOT", "."))
    tools = tmod.collect(root, snap.device)
    if query:
        q = query.lower()
        tools = [t for t in tools
                 if q in t.name.lower() or q in (t.summary or "").lower()]

    rows = max(1, height - 5)
    visible, offset, sel = _list.window(tools, sel, rows)
    count = _list.scrollbar(offset, len(visible), len(tools))
    win.addstr(1, 2, f"{len(tools)} tool(s)" + (f"  filter: {query}" if query
                                                else ""), T.attr(T.DIM))
    if count:
        win.addstr(1, max(0, width - len(count) - 2), count, T.attr(T.DIM))
    for i, tool in enumerate(visible):
        y = 3 + i
        needs = (getattr(tool, "needs", "") or "").upper()
        role = T.OK if needs in ("", "NONE", "ANY") else T.WARN
        win.addstr(y, 2, T.fit(tool.name, 26).ljust(26),
                   T.attr(T.BASE, reverse=(offset + i == sel)))
        win.addstr(y, 29, T.fit(needs or "-", 9).ljust(9), T.attr(role))
        win.addstr(y, 39, T.fit(tool.summary or "", max(0, width - 41)),
                   T.attr(T.DIM))
    return len(tools)


def keys():
    return [("enter", "read its contract"), ("/", "filter")]
