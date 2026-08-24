# SPDX-License-Identifier: MIT
"""The port pane: the phase spine, and the one next thing.

The signature screen. Its job is to answer, in one glance and without
scrolling: how far along am I, what is next, and is anything lying to me.

Everything here is a pure function of the snapshot and writes through a small
`win` protocol (`addstr(y, x, text, attr)`), so it renders into a recording
fake in tests. Curses is confined to app.py.
"""
from __future__ import annotations

from .. import theme as T

TITLE_W = 30


def render(win, snap, height, width, sel=0):
    """Draw the pane. Returns the number of selectable rows."""
    import porthole_milestones as ms
    from ..model import phases

    if not snap.device:
        win.addstr(1, 2, "No device selected.", T.attr(T.WARN))
        win.addstr(3, 2, "Press 2 to pick one, or run:", T.attr(T.BASE))
        win.addstr(4, 4, "porthole new-device <codename>", T.attr(T.NOTE))
        return 0

    rows = snap.rows or []
    ph = phases(rows)
    y = 1

    # -- the spine ---------------------------------------------------------
    # Numbered because the sequence is real. A port genuinely goes
    # before-device -> first-boot -> storage -> packaging -> subsystems ->
    # upstream, and skipping ahead is how weeks are lost.
    for i, p in enumerate(ph):
        if y >= height - 8:
            break
        mark = T.g("here") + " here" if p["current"] else ""
        role = T.ACTIVE if p["current"] else T.BASE
        win.addstr(y, 2, f"{i}", T.attr(T.DIM))
        win.addstr(y, 4, T.fit(p["name"], 18).ljust(18),
                   T.attr(role, bold=p["current"]))
        win.addstr(y, 23, T.bar(p["done"], p["total"]),
                   T.attr(T.OK if p["done"] else T.DIM))
        win.addstr(y, 33, f"{p['done']}/{p['total']}", T.attr(T.DIM))
        if p["stale"]:
            win.addstr(y, 40, f"{T.g('bang')}{p['stale']}", T.attr(T.CRIT))
        if mark:
            win.addstr(y, 44, mark, T.attr(T.ACTIVE, bold=True))
        y += 1

    y += 1

    # -- what is wrong -----------------------------------------------------
    # Stale first, always. A milestone ticked that the tool can see is false is
    # the one thing that must never scroll off: it means the port believes it
    # is further along than it is, and that is the direction that gets someone
    # flashing.
    problems = [r for r in rows if r["source"] == "stale"]
    problems += [r for r in rows if r["state"] == ms.BLOCKED]
    for row in problems[:max(0, (height - y - 7))]:
        stale = row["source"] == "stale"
        tag = f"{T.g('bang')} stale " if stale else f"{T.g('dot')} blocked"
        win.addstr(y, 2, tag, T.attr(T.CRIT if stale else T.WARN))
        win.addstr(y, 12, T.fit(row["title"], TITLE_W).ljust(TITLE_W),
                   T.attr(T.BASE))
        win.addstr(y, 12 + TITLE_W + 1,
                   T.fit(row["evidence"] or "", max(0, width - TITLE_W - 16)),
                   T.attr(T.DIM))
        y += 1

    # -- the answer --------------------------------------------------------
    nxt = (snap.summary or {}).get("next")
    y = max(y + 1, height - 6)
    if not nxt:
        win.addstr(y, 2, "Every milestone is done or blocked.", T.attr(T.OK))
        return 0

    win.addstr(y, 2, "next", T.attr(T.BASE, bold=True))
    win.addstr(y, 8, T.fit(nxt["title"], width - 12), T.attr(T.NOTE, bold=True))
    win.addstr(y + 1, 3, "why", T.attr(T.DIM))
    win.addstr(y + 1, 8, T.fit(nxt["why"], width - 12), T.attr(T.BASE))
    win.addstr(y + 2, 3, "how", T.attr(T.DIM))
    win.addstr(y + 2, 8, T.fit(nxt["command"] or "(no command)", width - 24),
               T.attr(T.NOTE))
    # Right-aligned from the label's own length, not a guessed offset: the
    # first version hard-coded one and printed "enter runs i".
    if nxt.get("safe") and nxt.get("command"):
        label = f"{T.g('arrow')} enter runs it"
        role = T.OK
    elif nxt.get("command"):
        # Never offer to run something irreversible. Saying WHY it is not
        # offered is the difference between a limitation and a bug.
        label, role = "needs confirming", T.WARN
    else:
        return 0
    win.addstr(y + 2, max(0, width - len(label) - 2), label, T.attr(role))
    return 0


def keys():
    return [("enter", "run the next step (when safe)"),
            ("r", "refresh")]
