#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What counts as a version, and what is a tool complaining about the question.

`porthole version` printed `unknown option -- -` as ssh's version for as long
as the row existed, and the fallback -- a bare `version` -- made ssh dial the
word as a HOSTNAME. Both answers were then pinned to an unchanged binary by
the cache, forever.

The excuses are verbatim, because that is the point: the next tool's wording
belongs in this file rather than on somebody's terminal.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_cmd_version as ver                          # noqa: E402

# Real first lines, captured from the tools porthole probes.
VERSIONS = (
    "OpenSSH_10.2p1, OpenSSL 3.6.1 8 Jul 2026",
    "git version 2.51.0",
    "podman version 5.7.1",
    "Python 3.14.0",
    "fastboot version 36.0.1-13206524",
)

# What those same tools say when they did not understand the flag. `ssh
# --version` is the one that shipped as an answer; the second line is what a
# per-line search would have taken instead once the first was rejected.
REFUSALS = (
    "unknown option -- -",
    "usage: ssh [-46AaCfGgKkMNnqsTtVvXxYy] [-B bind_interface]",
    "        [-c cipher_spec] [-D [bind_address:]port] [-E log_file]",
    "unrecognized option '--version'",
    "invalid option -- 'V'",
    "Pseudo-terminal will not be allocated because stdin is not a terminal.",
)


def test_versions_are_plausible():
    for line in VERSIONS:
        assert ver._plausible(line), line


def test_refusals_are_not_versions():
    for line in REFUSALS:
        assert not ver._plausible(line), line


def test_a_version_needs_a_digit():
    """The general test: every version string has one, no excuse does."""
    assert not ver._plausible("ssh: Could not resolve hostname version")
    assert not ver._plausible("")


def test_probe_is_in_the_cache_key():
    """A cached answer must not outlive the code that would no longer give it.

    Guards the half that would have kept the fix off every machine that had
    already run `porthole version`: ssh's binary does not move, so the wrong
    string stayed keyed to it.
    """
    source = (ROOT / "lib" / "porthole_cmd_version.py").read_text()
    assert "PROBE" in source.split("hashlib.sha256", 1)[1].split(")", 1)[0]


def test_bare_version_is_asked_last():
    """`ssh version` is a CONNECTION -- ssh reads the word as a hostname and
    dials it. It may only be reached once both flags that cannot dial
    anything have been refused."""
    source = (ROOT / "lib" / "porthole_cmd_version.py").read_text()
    asked = re.search(r"for flags in \((.*?)\):", source, re.S).group(1)
    assert [m for m in re.findall(r'"([^"]+)"', asked)] == [
        "--version", "-V", "version"], asked


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
