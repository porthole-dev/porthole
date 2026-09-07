#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole cd` -- print one path, so a shell can go there.

    cd "$(porthole cd)"           the device working repo
    cd "$(porthole cd pmaports)"  wherever pmbootstrap cloned it this time

Its own verb rather than an action on `use`, because its contract is different
from every other verb here: stdout is EXACTLY one path and nothing else. No
heading, no colour, no hint -- anything else and the command substitution puts
garbage into your shell. That contract is easier to keep in a file that does
nothing else.
"""
from __future__ import annotations

from porthole_cmd_use import TARGETS, cmd_cd

SPEC = {
    "verb": "cd",
    "order": 13,
    "group": "start",
    "help": "print a path to cd into: workdir, kernel, pmaports, profile",
    "description": (
        "Prints one bare path and nothing else, for command substitution:\n\n"
        "    cd \"$(porthole cd)\"\n"
        "    cd \"$(porthole cd pmaports)\"\n\n"
        "Exits non-zero with an explanation on stderr if the path is unset or\n"
        "missing, so a shell function can fail loudly instead of cd-ing to ~."),
    # Not a report: stdout is a path being substituted into a command line.
    "reports": False,
    "args": [
        (["target"], {"nargs": "?", "metavar": "WHAT",
                      "choices": list(TARGETS),
                      "help": " | ".join(TARGETS) + "  (default workdir)"}),
    ],
    "run": cmd_cd,
    "examples": [
        'cd "$(porthole cd)"',
        'cd "$(porthole cd kernel)"',
        'cd "$(porthole cd pmaports)"',
    ],
}
