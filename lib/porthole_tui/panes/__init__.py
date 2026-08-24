# SPDX-License-Identifier: MIT
"""Panes. Each exports `render(win, snap, height, width, sel)` and `keys()`.

One interface, so the router never grows a special case and a pane can be
rendered into a recording fake without a terminal.
"""
