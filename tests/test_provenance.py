#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Which kernel is the phone running, and where did it come from?

An agent built one module from a tree for an experiment and later moved to
venus work expecting the aport's 19 venus patches to be present. They were
not, and NOTHING said so: no /dev/video7, no venus module, nothing in dmesg or
the journal -- an entirely silent absence.

The stamp that answers this has been on every device all along. The kernel
APKBUILD sets KBUILD_BUILD_VERSION="$((pkgrel + 1))-$_flavor", so `uname -v`
names the aport release; an envkernel build never runs that line and carries
the tree's own counter with no flavor suffix.

The fixtures are read from the live phone, 2026-08-31.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_provenance as prov                          # noqa: E402

# Real, from the phone. Truncated by the kernel at 64 bytes -- utsname.version
# is a 65-byte field -- which is why the timestamp ends mid-second. The prefix
# is what this reads and it survives.
APORT = "#22-postmarketos-qcom-msm8998-7.2 SMP PREEMPT Sat Aug 29 14:59:5"

# An envkernel build: pmbootstrap packages objects the tree already compiled,
# so the APKBUILD's make line never runs and KBUILD_BUILD_VERSION is unset.
TREE = "#7 SMP PREEMPT Fri Aug 29 11:02:14 UTC 2026"


def test_an_aport_build_names_its_pkgrel():
    got = prov.parse_build_version(APORT)
    assert got["kind"] == "aport", got
    # pkgrel + 1 is what the APKBUILD stamps, so #22 means r21.
    assert got["pkgrel"] == 21, got
    assert got["flavor"] == "postmarketos-qcom-msm8998-7.2", got


def test_a_tree_build_has_no_flavor_and_is_not_an_aport():
    got = prov.parse_build_version(TREE)
    assert got["kind"] == "tree", got
    assert got["pkgrel"] is None, got


def test_unparseable_is_unknown_never_guessed():
    assert prov.parse_build_version("")["kind"] == "unknown"
    assert prov.parse_build_version("Linux version 7.2.2")["kind"] == "unknown"


def test_matching_pkgrel_is_done():
    running = {"build_version": APORT, "apk_pkgrel": "21"}
    state, evidence = prov.compare(running, "7.2.2", "21")
    assert state == "done", (state, evidence)
    assert "r21" in evidence, evidence


def test_a_device_behind_the_checkout_is_todo_and_names_both():
    running = {"build_version": APORT, "apk_pkgrel": "21"}
    state, evidence = prov.compare(running, "7.2.2", "22")
    assert state == "todo", (state, evidence)
    assert "r21" in evidence and "r22" in evidence, evidence


def test_boot_and_rootfs_desync_is_reported():
    """`fast` flashes boot only and leaves the rootfs apk db untouched.

    So uname says one pkgrel and the device's own apk database says another.
    That is the LADDER's documented trigger for the `kernel` rung and nothing
    detected it before.
    """
    running = {"build_version": APORT, "apk_pkgrel": "19"}
    state, evidence = prov.compare(running, "7.2.2", "21")
    assert state == "todo", (state, evidence)
    assert "desync" in evidence.lower(), evidence


def test_a_tree_build_is_blocked_and_says_the_series_may_be_absent():
    running = {"build_version": TREE, "apk_pkgrel": "21"}
    state, evidence = prov.compare(running, "7.2.2", "22")
    assert state == "blocked", (state, evidence)
    assert "tree" in evidence.lower(), evidence
    assert "patch" in evidence.lower(), evidence


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
