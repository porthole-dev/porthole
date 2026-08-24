# SPDX-License-Identifier: MIT
"""A scrolling viewport, shared by every list pane.

Written after the brain pane drew all 55 notes straight off the bottom of the
screen and let the selection run past the end. Each pane had its own `for i,
item in enumerate(...)` with a `break`, which is not a viewport -- it is a
truncation that happens to look like one until the list is longer than the
terminal.

One helper, so a pane cannot be written without scrolling by forgetting to add
it.
"""
from __future__ import annotations


def window(items, sel: int, rows: int):
    """(visible, offset, sel) -- the slice to draw and where the cursor is.

    Keeps the selection inside the viewport with a one-row margin, so moving
    with j/k scrolls before the cursor hits the edge rather than after.
    """
    total = len(items)
    if total == 0:
        return [], 0, 0
    rows = max(1, rows)
    sel = max(0, min(sel, total - 1))
    offset = 0
    if sel >= rows:
        offset = sel - rows + 1
    offset = max(0, min(offset, max(0, total - rows)))
    return items[offset:offset + rows], offset, sel


def scrollbar(offset: int, shown: int, total: int) -> str:
    """"12-30 of 55", or "" when everything fits.

    A count, not a graphical bar: the question a porter has is "am I seeing all
    of it", and a number answers that in a way a one-column glyph does not.
    """
    if total <= shown or not shown:
        return ""
    return f"{offset + 1}-{min(offset + shown, total)} of {total}"
