#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What the installed pmbootstrap supports, asked rather than assumed.

WHY
    porthole shells pmbootstrap for seven things. By 3.11.1 two of them were
    gone: the `lint` subcommand was removed, and `channel` stopped being a
    config key. Neither disappearance was noticed, because both failures were
    rendered as answers -- `porthole aports lint` printed "lint found
    problems" for a subcommand that does not exist, and `porthole channel`
    printed "current: unknown".

    AGENTS.md section 6 names that the cardinal sin: an agent that cannot tell
    "the tool broke" from "the answer is no" reports broken tools as findings.

    So: ask. A version number is the wrong question -- distro patches move the
    surface without moving the version -- but argparse names every valid
    choice when it rejects one, and that inventory cannot disagree with the
    binary that produced it.
"""
from __future__ import annotations

import re
import subprocess

# Every pmbootstrap call porthole makes UNCONDITIONALLY. tests/test_pmb_api.py
# asserts each one exists in the installed pmbootstrap, which is what stops
# this drifting again silently.
#
# WHY lint and channel are absent: porthole also calls `lint` and `config
# channel`, but Tasks 2 and 3 wrap both with missing() first. A call guarded
# by missing() degrades gracefully (e.g. "lint not available" instead of
# "lint failed"), so listing it here would fail the guard on every pmbootstrap
# that dropped it -- which is every one today. Only unconditional calls belong
# here.
PORTHOLE_USES = {
    "subcommands": frozenset({
        "build", "checksum", "ci", "config", "kconfig", "pkgrel_bump",
    }),
    "config_keys": frozenset({"ui"}),
}

_CHOICES = re.compile(r"\{([a-z0-9_,]+)\}")
_CHOOSE_FROM = re.compile(r"choose from ([^)]+)")


def subcommands(help_text: str) -> set:
    """The subcommands named in pmbootstrap's own help."""
    found = set()
    for match in _CHOICES.finditer(help_text):
        found.update(part for part in match.group(1).split(",") if part)
    return found


def config_keys(err_text: str) -> set:
    """The config keys argparse named when it rejected an invalid one."""
    match = _CHOOSE_FROM.search(err_text)
    if not match:
        return set()
    return {part.strip().strip("'\"")
            for part in match.group(1).split(",") if part.strip()}


def supports(available: set, name: str) -> bool:
    return name in available


def _run(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


_CACHE = {}


def probe(runner=None) -> dict:
    """Ask the installed pmbootstrap what it has.

    Cached for the process: two subprocess calls is cheap once and wasteful
    on every verb. `runner` is injectable so the tests never shell out.
    """
    if "result" in _CACHE and runner is None:
        return _CACHE["result"]
    run = runner or _run
    result = {
        "subcommands": subcommands(run(["pmbootstrap", "--help"])),
        # An invalid key makes argparse list every valid one. Asking for the
        # list directly is not offered by the CLI.
        "config_keys": config_keys(
            run(["pmbootstrap", "config", "__porthole_probe__"])),
        "version": run(["pmbootstrap", "--version"]).strip(),
    }
    if runner is None:
        _CACHE["result"] = result
    return result


def missing(kind: str, name: str) -> str:
    """"" if pmbootstrap has it, else a sentence saying it does not.

    The message names the version, because "your pmbootstrap does not have
    this" is only actionable if you know which pmbootstrap answered.
    """
    have = probe()
    if not have[kind]:
        return ""  # could not ask; do not invent a refusal
    if name in have[kind]:
        return ""
    version = have.get("version") or "this pmbootstrap"
    noun = "subcommand" if kind == "subcommands" else "config key"
    return f"{version} has no `{name}` {noun}"
