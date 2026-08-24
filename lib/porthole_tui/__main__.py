# SPDX-License-Identifier: MIT
"""`python3 -m porthole_tui` -- the console's entry point.

The gate runs before anything imports textual, so a host without it gets a
sentence it can act on instead of a traceback.
"""
from __future__ import annotations

import os
import pathlib
import sys

from . import gate


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not sys.stdout.isatty():
        sys.stderr.write("porthole's console needs a terminal. "
                         "For a pipe or an agent, use `porthole next --json`.\n")
        return 64
    problem = gate.check()
    if problem:
        sys.stderr.write(problem)
        return 1
    device = None
    for flag in ("-d", "--device"):
        if flag in argv:
            idx = argv.index(flag)
            if idx + 1 < len(argv):
                device = argv[idx + 1]
    os.environ.setdefault("ESCDELAY", "25")
    from .app import PortholeApp        # after the gate, never before
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    return PortholeApp(root, device).run() or 0


if __name__ == "__main__":
    sys.exit(main())
