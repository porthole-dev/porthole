# SPDX-License-Identifier: MIT
"""`porthole version` -- what am I running, and in what environment.

More than a version string: the environment summary is what you paste into a
bug report, and what an agent should include when reporting that something
does not work on a host it cannot otherwise describe.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys

from porthole_cli import EX_OK, version


def tool_version(name: str) -> str:
    path = shutil.which(name)
    if not path:
        return "not found"
    for flags in (["--version"], ["version"]):
        try:
            proc = subprocess.run([path, *flags], capture_output=True,
                                  text=True, timeout=5)
            line = (proc.stdout or proc.stderr).strip().splitlines()
            if line:
                return line[0][:60]
        except (OSError, subprocess.TimeoutExpired):
            continue
    return path


def cmd_version(args, ctx) -> int:
    import porthole_cmd_doctor as doc

    payload = {
        "porthole": version(ctx.root),
        "root": str(ctx.root),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "distro_family": doc.distro_family(),
        "git": _git(ctx.root),
        "tools": {name: tool_version(name)
                  for name in ("fastboot", "adb", "ssh", "pmbootstrap")},
    }

    def render():
        ctx.out.heading(f"porthole {payload['porthole']}")
        width = 14
        for key in ("root", "python", "platform", "distro_family", "git"):
            ctx.out.kv(key, str(payload[key]), width)
        ctx.out.blank()
        ctx.out.heading("host tools")
        for name, ver in payload["tools"].items():
            colour = "grey" if ver == "not found" else None
            ctx.out.kv(name, ctx.out.paint(ver, colour) if colour else ver, width)

    return ctx.emit(payload, render)


def _git(root) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(root), "describe", "--always",
                               "--dirty", "--tags"],
                              capture_output=True, text=True, timeout=5)
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


SPEC = {
    "verb": "version",
    "order": 95,
    "help": "version, environment and host tool versions",
    "description": "What to paste into a bug report.",
    "args": [(["--json"], {"action": "store_true", "help": "machine-readable"})],
    "run": cmd_version,
    "examples": ["porthole version", "porthole version --json"],
}
