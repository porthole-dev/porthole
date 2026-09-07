#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole tui` -- launch the console.

A launcher, so the TUI is discoverable from `porthole --help` rather than being
a second binary nobody finds. The CLI runs `python3 -m porthole_tui`, which owns
the terminal; keeping that separate means no verb ever unexpectedly seizes the
screen in a script.
"""
from __future__ import annotations

import os
import pathlib
import sys

from porthole_cli import Bail, EX_FAIL, EX_USAGE, child_env


def cmd_tui(args, ctx) -> int:
    if not sys.stdout.isatty():
        raise Bail("the console needs a terminal", EX_USAGE,
                   "for a pipe or an agent, use `porthole next --json`")
    # Run the gate HERE as well as in __main__, so a missing dependency is a
    # Bail with a hint rather than a subprocess that prints and exits 1.
    lib = pathlib.Path(ctx.root) / "lib"
    sys.path.insert(0, str(lib))
    from porthole_tui import gate
    problem = gate.check()
    if problem:
        raise Bail(problem.rstrip(), EX_FAIL)
    argv = [sys.executable, "-m", "porthole_tui"]
    device = getattr(args, "device", None) or ctx.cfg.get("PORTHOLE_DEVICE", "")
    if device:
        argv += ["-d", device]
    env = child_env(os.environ)
    env["PYTHONPATH"] = str(lib) + os.pathsep + env.get("PYTHONPATH", "")
    os.execve(sys.executable, argv, env)   # replace: two processes for one screen
    return EX_FAIL                         # unreachable


SPEC = {
    "verb": "tui",
    "order": 11,
    "group": "knowledge",
    "help": "open the console: progress, devices, tools, notes, in one screen",
    "description": (
        "An always-open front end over the same library. It loads the config,\n"
        "the registry, the pmaports index and the milestone state once, so a\n"
        "keystroke costs nothing where a CLI call costs a process.\n\n"
        "Read plus safe actions. Anything that flashes, writes to the device\n"
        "or touches a slot needs an explicit confirmation naming the exact\n"
        "command."),
    # It seizes the terminal; there is no report and no JSON to give.
    "reports": False,
    "args": [],
    "run": cmd_tui,
    "examples": ["porthole tui", "porthole tui -d google-cheetah"],
}
