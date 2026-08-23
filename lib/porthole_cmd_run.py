# SPDX-License-Identifier: MIT
"""`porthole run` -- run a tool with the config applied.

Optional convenience, never mandatory: tools resolve config themselves, so
`tools/tk-fps.py` works directly and always will. This exists for the cases
where it genuinely helps -- running a profile-scoped tool without knowing which
directory it landed in, and holding the device mutex without spelling it out.
"""
from __future__ import annotations

import os
import subprocess

from porthole_cli import Bail, EX_FAIL
from porthole_cmd_tools import collect


def cmd_run(args, ctx) -> int:
    cfg = ctx.cfg
    device = cfg.get("PORTHOLE_DEVICE", "")
    tools = {t.name: t for t in collect(ctx.root, device)}
    tools.update({t.path.stem: t for t in collect(ctx.root, device)
                  if t.path.stem not in tools})

    tool = tools.get(args.tool)
    if tool is None:
        near = [n for n in tools if args.tool in n]
        raise Bail(f"no tool named {args.tool!r}", EX_FAIL,
                   f"did you mean: {', '.join(sorted(near)[:5])}?" if near
                   else "`porthole tools` lists them all")

    env = dict(os.environ)
    env.update({k: str(v) for k, v in cfg.items()})
    env["PORTHOLE_ROOT"] = str(ctx.root)

    argv = [str(tool.path), *args.args]
    if args.lock:
        # Declaring the state is the whole point of the mutex: exit 76 in a
        # second beats queueing ten minutes for a device that was never going
        # to answer. See brain/laws/the-lock-says-who-not-what.md.
        need = tool.needs.upper()
        wrapper = [str(ctx.root / "tools" / "tk-device.sh")]
        if need in ("BOOTED", "FASTBOOT"):
            wrapper.append(f"--need-{need.lower()}")
        env.setdefault("TK_AGENT", cfg.get("PORTHOLE_AGENT") or os.environ.get("USER", "porthole"))
        argv = wrapper + argv

    return subprocess.run(argv, env=env).returncode


SPEC = {
    "verb": "run",
    "order": 60,
    "help": "run a tool with the config applied",
    "description": (
        "Optional. Tools resolve config themselves, so `tools/tk-fps.py` works\n"
        "directly. Use this to reach a profile-scoped tool by name, or with\n"
        "--lock to take the device mutex with the right state declared."),
    "args": [
        (["tool"], {"help": "tool name, with or without extension"}),
        (["--lock"], {"action": "store_true",
                      "help": "hold the device mutex, declaring the tool's needs"}),
        (["args"], {"nargs": "...", "help": "arguments passed to the tool"}),
    ],
    "run": cmd_run,
    "examples": [
        "porthole run tk-fps.py",
        "porthole run --lock tk-suspend-cycle.sh 20",
    ],
}
