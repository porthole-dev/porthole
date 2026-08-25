# SPDX-License-Identifier: MIT
"""porthole's console: an always-open front end over the same library.

A second front end, not a replacement. Every action here has a verb behind it,
because agents drive verbs and a capability reachable only by a human is one
agents cannot use.

Optional: this package needs python 3.10 and textual. The CLI needs neither.
Nothing in lib/porthole_cmd_*.py may import from here, except
lib/porthole_cmd_tui.py, and only `gate`.
"""
