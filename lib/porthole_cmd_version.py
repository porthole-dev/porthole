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


def _tool_versions(names) -> dict:
    """Host tool versions, cached on each binary's identity.

    `pmbootstrap --version` starts a second Python interpreter and costs 223ms
    of a 240ms command; fastboot, adb and ssh are ~6ms each. Concurrency was
    tried and made it WORSE -- the pool cost 70ms and saved nothing, because
    one probe dominates and threads cannot make it finish sooner.

    So cache instead, keyed on the resolved path plus its mtime and size. A
    tool's version changes when its binary does, which is exactly what that
    key tracks; nothing else can change it.
    """
    import hashlib
    import pathlib
    import json
    import os
    import shutil

    base = os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache")
    cache = pathlib.Path(base) / "porthole" / "tool-versions.json"
    try:
        blob = json.loads(cache.read_text())
    except Exception:  # noqa: BLE001
        blob = {}

    out, dirty = {}, False
    for name in names:
        path = shutil.which(name)
        if not path:
            out[name] = ""
            continue
        try:
            st = os.stat(path)
            key = hashlib.sha256(
                f"{path}:{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:16]
        except OSError:
            out[name] = tool_version(name)
            continue
        if blob.get(name, {}).get("key") == key:
            out[name] = blob[name]["version"]
        else:
            out[name] = tool_version(name)
            blob[name] = {"key": key, "version": out[name]}
            dirty = True

    if dirty:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(blob))
        except OSError:
            pass
    return out


def cmd_version(args, ctx) -> int:
    import porthole_cmd_doctor as doc

    payload = {
        "porthole": version(ctx.root),
        "root": str(ctx.root),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "distro_family": doc.distro_family(),
        "git": _git(ctx.root),
        # Concurrently: each of these starts a process, and pmbootstrap
        # starts a whole second Python interpreter -- 0.24s of a 0.29s command
        # spent serially waiting on four subprocesses that do not need each
        # other.
        "tools": _tool_versions(("fastboot", "adb", "ssh", "pmbootstrap")),
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
