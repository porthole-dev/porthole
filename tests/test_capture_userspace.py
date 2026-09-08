#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Capturing the device's locally-built package set before a wipe.

A from-scratch install reinstalls userspace from the local repo. On
2026-09-08 the device carried gst-plugins-good-1.28.5-r1 while the repo
held r50: the wipe would have silently swapped a hand-built package for a
different one. The manifest is what makes that a decision instead of an
accident.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "ph-capture-userspace.sh"


def _parse(text):
    """The tool's own parser, exercised through `--parse-only`."""
    out = subprocess.run(["bash", str(TOOL), "--parse-only"], input=text,
                         capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_only_locally_built_packages_are_captured():
    """`apk info -v` lists everything. Only what the local repo built can be
    restored from it, and only that is at risk from a wipe."""
    text = ("mesa-26.1.6-r14\n"
            "musl-1.2.5-r9\n"
            "webkit2gtk-6.0-2.52.6-r63\n")
    got = _parse(text)
    assert "mesa-26.1.6-r14" in got
    assert "webkit2gtk-6.0-2.52.6-r63" in got
    assert "musl-1.2.5-r9" not in got, "an upstream package is not ours to restore"


def test_the_exact_pkgrel_is_kept_not_just_the_name():
    """gst-plugins-good on the device was r1 and in the repo r50. A manifest
    that recorded only the name would restore the wrong build and report
    success."""
    got = _parse("gst-plugins-good-1.28.5-r1\n")
    assert got == ["gst-plugins-good-1.28.5-r1"]


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
