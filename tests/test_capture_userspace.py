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


def _parse_raw(text):
    """Full stdout, comment lines included -- `_parse`'s `.split()` would
    tear a `# excluded: ...` line into separate tokens."""
    out = subprocess.run(["bash", str(TOOL), "--parse-only"], input=text,
                         capture_output=True, text=True, check=True)
    return out.stdout


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


# ---------------------------------------------------- device/firmware --
#
# Confirmed live on this hardware, not theoretical: the sandbox repo builds
# device-google-taimen, device-google-taimen-kernel-mainline,
# device-google-taimen-openrc and firmware-google-taimen, and the device has
# them installed. Restoring them with `apk add` right after a from-scratch
# install would run at Gate C, on a freshly flashed phone --
# brain/traps/installing-firmware-can-flash-the-boot-partition.md and
# brain/traps/a-sideloaded-device-apk-can-eat-the-radio-stack.md say what
# happens next. They stay in the manifest, as evidence of what was on the
# device, but as a `# excluded:` comment `restore`'s own `grep -v '^#'`
# already skips -- one mechanism, not a second filter to keep in sync.

def test_a_firmware_package_is_excluded_from_the_restorable_set():
    raw = _parse_raw("firmware-google-taimen-20250505-r4\n")
    # A restorable line is the bare name-version, with no "#" -- checking
    # `.split()` tokens would find the name INSIDE the excluded comment too.
    assert "firmware-google-taimen-20250505-r4" not in raw.splitlines(), \
        "a firmware package must never reach restore's apk add"
    assert "# excluded: firmware-google-taimen-20250505-r4" in raw.splitlines()


def test_a_device_package_is_excluded_the_same_way():
    raw = _parse_raw("device-google-taimen-1-r40\n")
    assert "device-google-taimen-1-r40" not in raw.splitlines()
    assert "# excluded: device-google-taimen-1-r40" in raw.splitlines()


def test_device_mapper_is_not_mistaken_for_a_device_package():
    """device-mapper is upstream, not a device-<vendor> package this repo
    builds -- it merely starts with the same four letters. The exclusion has
    to key off what the local repo builds, not off the string "device-", or
    an unrelated package the box never built gets misfiled as "excluded"."""
    raw = _parse_raw("device-mapper-2.03.35-r6\n")
    assert "excluded" not in raw
    assert raw.split() == [], "not locally built, so not restorable either"


def test_a_plain_package_is_still_captured_unmarked():
    got = _parse("mesa-26.1.6-r14\n")
    assert got == ["mesa-26.1.6-r14"]


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
