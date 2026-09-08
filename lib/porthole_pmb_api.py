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
# WHY `lint` and `channel` are absent: porthole still calls both, but never
# unconditionally. `aports lint` and the `channel <name> --yes` switch each
# ask missing() first and bail EX_UNAVAILABLE (69) when the answer is no, so
# they degrade into "this pmbootstrap has no lint" rather than into a finding
# about the user's packages. Reading the current channel no longer shells
# pmbootstrap at all -- it parses pmaports.cfg (456cb3b).
#
# Listing a guarded call here would fail the guard below on every pmbootstrap
# that dropped it, which is every one today. Only unconditional calls belong
# here.
#
# Membership is CHECKED against the tree, not kept by hand: this named seven
# while porthole invoked twelve, and `export` -- what `porthole build image`
# ends with -- was one of the five missing, so a rename upstream would have
# left the suite green and broken the build in the field.
# tests/test_pmb_api.py::test_the_guard_names_every_subcommand_porthole_invokes
# compares it against `invoked_subcommands`.
PORTHOLE_USES = {
    "subcommands": frozenset({
        "aportgen", "build", "checksum", "chroot", "ci", "config", "export",
        "flasher", "index", "install", "kconfig", "pkgrel_bump",
    }),
    "config_keys": frozenset({"ui"}),
}

# Calls deliberately NOT in PORTHOLE_USES because each one asks `missing()`
# first and degrades into "this pmbootstrap has no X" rather than into a
# finding about the user's packages. See this module's docstring.
GUARDED_AT_CALL_SITE = frozenset({"lint"})

# An argv list whose first element is pmbootstrap, or a shell command in
# COMMAND POSITION starting with the word -- start of line, or right after a
# control keyword (if/elif/while/until/then/do/else) or an operator
# (;  &&  ||  |  !). That still excludes prose: every mention of pmbootstrap
# in this repo's many comments is either the first word after `#` (not a
# control keyword or operator) or embedded mid-sentence ("pmbootstrap's",
# "pmbootstrap never hits this"), so none of them sit in command position.
# `if pmbootstrap install ...; then` used to slip past the old start-of-line
# anchor entirely -- losing `install`, the subcommand every install rung
# depends on, and losing it silently: see this module's own warning below
# about a scan that finds too little.
_CALL_PY = re.compile(r'"pmbootstrap"(?:,\s*"-[^"]*")*,\s*"([a-z_]+)"')
_CALL_SH = re.compile(
    r'(?:^[ \t]*|[;&|!]+[ \t]*|\b(?:if|elif|while|until|then|do|else)\b[ \t]+)'
    r'pmbootstrap(?:\s+-\S+)*\s+([a-z_]+)\b', re.M)
# porthole_cmd_aports.pmb() builds the argv itself, so its callers name the
# subcommand and pmbootstrap never appears beside it. Without this the scan is
# blind to checksum, lint and pkgrel_bump -- which happen to be hand-listed
# today, so the omission would not have shown up as a failure. It would have
# shown up as the NEXT one being missed.
_CALL_HELPER = re.compile(r'\bpmb\(\s*ctx\s*,\s*"([a-z_]+)"')


def invoked_subcommands(root) -> set:
    """Every pmbootstrap subcommand this tree actually invokes.

    Reads the source rather than taking a hand-kept list, because a hand-kept
    list is exactly what went stale: the guard named seven while the tree
    called eighteen, and nothing could tell.
    """
    import pathlib

    root = pathlib.Path(root)
    found = set()
    for path in sorted((root / "lib").glob("*.py")):
        text = path.read_text(errors="replace")
        found.update(_CALL_PY.findall(text))
        found.update(_CALL_HELPER.findall(text))
    for path in sorted((root / "tools").glob("*.sh")):
        # _CALL_PY too: a Python heredoc inside a .sh file (see
        # _ph_assemble_image in tools/ph-build.sh) calls pmbootstrap with the
        # same `["pmbootstrap", ..., "sub"]` argv shape _CALL_PY already
        # knows, and _CALL_SH cannot see into it -- it is python text sitting
        # inside a shell file, not a shell command.
        text = path.read_text(errors="replace")
        found.update(_CALL_SH.findall(text))
        found.update(_CALL_PY.findall(text))
    return found


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
