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
    # `-V` BEFORE the bare `version` subcommand, and both after `--version`.
    # ssh has no `--version` (it answers `unknown option -- -` on stderr,
    # which this reported verbatim as ssh's version for as long as the row
    # existed) -- and it reads a bare `version` as a HOSTNAME and tries to
    # connect to it, which is a network round trip inside `porthole version`
    # and printed `Pseudo-terminal will not be allocated` as the answer.
    for flags in (["--version"], ["-V"], ["version"]):
        try:
            proc = subprocess.run([path, *flags], capture_output=True,
                                  text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        lines = [line.strip() for line
                 in (proc.stdout or proc.stderr).strip().splitlines()
                 if line.strip()]
        # A refusal invalidates the whole ATTEMPT, not just its first line: a
        # usage message is many lines and the second of ssh's is
        # `[-c cipher_spec] [-D [bind_address:]port] ...`, which is no more a
        # version than the `unknown option` above it.
        if lines and _plausible(lines[0]):
            return lines[0][:60]
    return path


# What a tool says when it did not understand the flag. Any of these means
# "ask again differently", never "this is the version".
_REFUSALS = ("unknown option", "unrecognized option", "invalid option",
             "usage:", "usage :")

# Bump when `tool_version` changes how it asks or what it accepts. The cache
# below is keyed on this as well as on the binary, so an answer this code
# would no longer give cannot outlive it.
PROBE = 2


def _is_refusal(line: str) -> bool:
    low = line.lower()
    return any(low.startswith(bad) or bad in low[:40] for bad in _REFUSALS)


def _plausible(line: str) -> bool:
    """Does this line actually look like a version?

    A digit is the cheap, general test -- every version string has one and
    none of the things this kept mistaking for one does: a refusal, a usage
    line, an ssh connection banner. Pure, so the wording of the next tool's
    excuse can be added to a test rather than discovered on somebody's
    terminal.
    """
    return bool(line) and not _is_refusal(line) and any(c.isdigit()
                                                        for c in line)


def _tool_versions(names) -> dict:
    """Host tool versions, cached on each binary's identity.

    `pmbootstrap --version` starts a second Python interpreter and costs 223ms
    of a 240ms command; fastboot, adb and ssh are ~6ms each. Concurrency was
    tried and made it WORSE -- the pool cost 70ms and saved nothing, because
    one probe dominates and threads cannot make it finish sooner.

    So cache instead, keyed on the resolved path plus its mtime and size --
    and on `PROBE`, which is the half that was missing. A tool's version
    changes when its binary does, and also when the code that ASKS changes:
    fixing ssh's row (`unknown option -- -`, cached against a binary that has
    not moved in months) would otherwise have reached nobody who had run this
    before, because the wrong answer was keyed on a binary that is still
    identical.
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
                f"{PROBE}:{path}:{st.st_mtime_ns}:{st.st_size}".encode()
            ).hexdigest()[:16]
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
    "group": "meta",
    "help": "version, environment and host tool versions",
    "description": "What to paste into a bug report.",
    "args": [(["--json"], {"action": "store_true", "help": "machine-readable"})],
    "run": cmd_version,
    "examples": ["porthole version", "porthole version --json"],
}
