#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole pkg owned` -- the one command ph-pkgcheck.sh asks."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402

PORTHOLE = str(ROOT / "bin" / "porthole")


def run(*args):
    return subprocess.run([PORTHOLE, "-d", "google-taimen", "pkg", "owned",
                           *args], capture_output=True, text=True)


def test_plain_output_is_bare_names_one_per_line():
    got = run("--tier", "required")
    assert got.returncode == 0, got.stderr
    lines = [ln for ln in got.stdout.splitlines() if ln.strip()]
    assert "mesa" in lines, lines
    # Bare names only: a shell reads this with $(...), so a decorated line
    # would become an argument.
    for line in lines:
        assert " " not in line.strip(), line


def test_required_excludes_the_optional_tier():
    assert "webkit2gtk-6.0" not in run("--tier", "required").stdout
    assert "webkit2gtk-6.0" in run("--tier", "optional").stdout


def test_json_carries_the_why():
    got = run("--json")
    assert got.returncode == 0, got.stderr
    payload = json.loads(got.stdout)
    assert payload["aports"]["mesa"]["why"]


def test_tier_filters_json_too():
    got = run("--tier", "required", "--json")
    assert got.returncode == 0, got.stderr
    payload = json.loads(got.stdout)
    assert "mesa" in payload["aports"], payload["aports"]
    assert "webkit2gtk-6.0" not in payload["aports"], payload["aports"]


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
