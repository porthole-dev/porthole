# SPDX-License-Identifier: MIT
"""Console: the channel that works before anything else does.

A UART needs no userspace, no driver probe and no initramfs. It is the only
thing that speaks during early boot and the only thing that says anything when
a kernel dies before console handover -- which is why it gets a pane rather
than living behind a subcommand you remember to run.

The pane does not open the port itself. `porthole serial` already owns that,
including the termios handling, and two implementations of a serial terminal in
one project is one too many.
"""
from __future__ import annotations

from .. import theme as T


def render(win, snap, height, width, sel=0, lines=None):
    lines = lines or []
    if not lines:
        win.addstr(1, 2, "No console attached.", T.attr(T.WARN))
        win.addstr(3, 2, "A UART is the only channel that works before "
                         "userspace exists.", T.attr(T.DIM))
        win.addstr(4, 2, "Find one, then attach:", T.attr(T.BASE))
        win.addstr(5, 4, "porthole serial hardware", T.attr(T.NOTE))
        win.addstr(6, 4, "porthole serial list", T.attr(T.NOTE))
        win.addstr(7, 4, "porthole serial console --log boot.log",
                   T.attr(T.NOTE))
        return 0
    for i, line in enumerate(lines[-(height - 4):]):
        role = T.BASE
        low = line.lower()
        # Colour by severity, the same way the palette was chosen: the eye is
        # already trained to find these words in a wall of kernel output.
        if any(w in low for w in ("panic", "oops", "bug:", "fatal", "error")):
            role = T.CRIT
        elif any(w in low for w in ("warn", "fail", "timeout")):
            role = T.WARN
        win.addstr(2 + i, 2, T.fit(line.rstrip(), width - 4), T.attr(role))
    return len(lines)


def keys():
    return [("a", "attach a serial console")]
