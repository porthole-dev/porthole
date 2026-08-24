# SPDX-License-Identifier: MIT
"""Colour, glyphs, and honest degradation.

The palette is not chosen for prettiness -- it is the **kernel log severity
palette**, because that is what a porter's eyes are already trained on. Red is
what dmesg makes red. Yellow is what it makes yellow. Nothing here asks anyone
to learn a second colour vocabulary while debugging a device that will not boot.

Degradation follows the same rule the CLI's `Out` already follows: a bring-up
host is often a minimal container with an 8-colour TERM and an ASCII locale, and
a tool that renders as mojibake there has failed at the exact moment it was
needed. Every glyph has an ASCII twin; every colour has a plain fallback.
"""
from __future__ import annotations

import curses
import os

# Semantic roles, not colour names. Call sites say what a thing MEANS -- and
# then the palette can change without hunting for every place "red" was typed.
CRIT = "crit"        # a stale claim, a failure, a forbidden slot
WARN = "warn"        # blocked, drifted, unprobed
OK = "ok"            # derived-done: a measured fact
NOTE = "note"        # an action you can take
DIM = "dim"          # evidence and provenance
ACTIVE = "active"    # the selected device; the "you are here"
BASE = "base"
BAR = "bar"          # chrome: header and footer

_PAIRS = {
    CRIT: (curses.COLOR_RED, -1),
    WARN: (curses.COLOR_YELLOW, -1),
    OK: (curses.COLOR_GREEN, -1),
    NOTE: (curses.COLOR_CYAN, -1),
    DIM: (8, -1),                       # bright black; falls back below
    ACTIVE: (curses.COLOR_MAGENTA, -1),
    BASE: (-1, -1),
}

_index: dict[str, int] = {}
_colour = False


def init() -> None:
    """Set up pairs once. Safe on a terminal with no colour at all."""
    global _colour
    _colour = False
    if os.environ.get("NO_COLOR"):
        return
    try:
        curses.start_color()
        curses.use_default_colors()
    except curses.error:
        return
    if curses.COLORS < 8:
        return
    for n, (name, (fg, bg)) in enumerate(_PAIRS.items(), start=1):
        # A 16-colour terminal has no bright black; grey becomes plain.
        if fg == 8 and curses.COLORS < 16:
            fg = -1
        try:
            curses.init_pair(n, fg, bg)
            _index[name] = n
        except curses.error:
            pass
    _colour = bool(_index)


def attr(role: str, bold: bool = False, reverse: bool = False) -> int:
    a = curses.A_BOLD if bold else 0
    if reverse:
        a |= curses.A_REVERSE
    if _colour and role in _index:
        a |= curses.color_pair(_index[role])
    elif role in (CRIT, ACTIVE):
        # No colour: keep the two roles that carry danger distinguishable.
        a |= curses.A_BOLD
    elif role == DIM:
        a |= curses.A_DIM
    return a


_UNICODE = "utf" in (os.environ.get("LANG", "")
                     + os.environ.get("LC_ALL", "")).lower()

_GLYPHS = {
    "full": ("█", "#"), "empty": ("░", "."), "here": ("◀", "<"),
    "tick": ("✓", "+"), "cross": ("✗", "x"), "dot": ("·", "-"),
    "bang": ("!", "!"), "arrow": ("→", "->"), "sep": ("─", "-"),
    "vert": ("│", "|"), "tl": ("┌", "+"), "tr": ("┐", "+"),
    "bl": ("└", "+"), "br": ("┘", "+"), "cross_t": ("├", "+"),
    "cross_r": ("┤", "+"), "ellipsis": ("…", "~"),
}


def g(name: str) -> str:
    fancy, plain = _GLYPHS[name]
    return fancy if _UNICODE else plain


def bar(done: int, total: int, width: int = 8) -> str:
    """A fill bar. Eight cells: enough to see movement, too few to become a
    dashboard. A port's progress is a fact, not a metric to optimise."""
    if total <= 0:
        return g("empty") * width
    filled = round(width * done / total)
    return g("full") * filled + g("empty") * (width - filled)


def fit(text: str, width: int) -> str:
    """Trim on a word boundary. Cutting mid-word reads as a rendering bug."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    cut = text[:max(0, width - 1)].rsplit(" ", 1)[0]
    return (cut + g("ellipsis"))[:width]
