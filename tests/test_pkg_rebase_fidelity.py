#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One property, and the awkward aports that break it.

THE PROPERTY
    Rebasing an aport where NOTHING changed -- base, ours and upstream all
    identical -- must reproduce that aport exactly. Content, file mode, and
    symlink-ness. If a no-op rebase cannot round-trip a tree, no rebase of
    that tree can be trusted, because every difference it reports is
    indistinguishable from a difference it invented.

WHY IT IS ITS OWN FILE
    `test_pkg_rebase.py` tests the merge DECISIONS -- which side wins, what
    conflicts. This tests FIDELITY, which is a different question and the one
    that was wrong: the first implementation read every file with
    `read_text()` and wrote every file with `write_text()`, so it silently
    turned 1084 symlinks into regular files holding a path, dropped the
    executable bit on 7 scripts, and mangled every binary. 382 of pmaports'
    1664 aports carry at least one such file.

    Table-driven on purpose. The named shapes below are the real ones, taken
    from pmaports on 2026-09-10; adding a row is how the next family of this
    bug gets closed, and each row fails loudly rather than being skipped.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_cmd_pkg as pkg                              # noqa: E402


def git(repo, *args):
    return subprocess.run(("git", "-C", str(repo)) + args,
                          capture_output=True, text=True, check=True)


# name -> how to build it. Each is a real shape from pmaports:
#   symlink     device/archived/device-cutiepi-tablet/*-openrc.post-upgrade
#   executable  device/testing/device-google-taimen/powerhintd
#   binary      device/archived/device-sony-taoshan/logo.rle
#   crlf        patches carrying CRLF survive git verbatim and must here too
SHAPES = {
    "plain.patch": ("regular", b"--- a\n+++ b\n"),
    "run.sh": ("exec", b"#!/bin/sh\necho hi\n"),
    "link.post-upgrade": ("symlink", b"run.sh"),
    "logo.rle": ("binary", bytes(range(256)) * 4),
    "crlf.patch": ("regular", b"--- a\r\n+++ b\r\n"),
}


def _build(ap: pathlib.Path):
    (ap / "APKBUILD").write_bytes(b"pkgname=x\npkgver=1\npkgrel=0\n")
    for name, (kind, blob) in SHAPES.items():
        target = ap / name
        if kind == "symlink":
            target.symlink_to(blob.decode())
            continue
        target.write_bytes(blob)
        if kind == "exec":
            os.chmod(target, 0o755)


def _repo_with_aport(tmp: pathlib.Path):
    up = tmp / "aports_upstream"
    ap = up / "device" / "device-x"
    ap.mkdir(parents=True)
    subprocess.run(("git", "init", "-qb", "master", str(up)), check=True,
                   capture_output=True)
    git(up, "config", "user.email", "t@example.invalid")
    git(up, "config", "user.name", "t")
    git(up, "config", "core.autocrlf", "false")
    _build(ap)
    git(up, "add", "-A")
    git(up, "commit", "-qm", "seed")
    return up, "device/device-x"


def _describe(d: pathlib.Path) -> dict:
    """{name: (kind, bytes)} for a directory, as git would see it."""
    got = {}
    for f in sorted(d.iterdir()):
        if f.is_symlink():
            got[f.name] = ("symlink", os.readlink(f).encode())
        elif f.is_file():
            kind = "exec" if f.stat().st_mode & 0o111 else "regular"
            got[f.name] = (kind, f.read_bytes())
    return got


def test_a_no_op_rebase_reproduces_the_aport_exactly():
    """base == ours == theirs. Anything that differs, the rebase invented."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        up, rel = _repo_with_aport(tmp)
        want = _describe(up / rel)

        tree = pkg._tree_at(up, "HEAD", rel)
        plan = pkg.plan_rebase(tree, tree, tree)

        out = tmp / "out"
        out.mkdir()
        pkg.write_plan(out, plan)
        got = _describe(out)

        assert set(got) == set(want), (
            f"files lost or invented: missing {sorted(set(want) - set(got))}, "
            f"extra {sorted(set(got) - set(want))}")
        for name in sorted(want):
            assert got[name] == want[name], (
                f"{name}: rebase wrote {got[name][0]} {got[name][1][:60]!r}, "
                f"git has {want[name][0]} {want[name][1][:60]!r}")

        assert not [v for v in plan.values() if v["verdict"] == "conflict"], \
            "a no-op rebase reported a conflict"


def test_a_nested_file_is_never_silently_dropped():
    """43 aports keep files in a subdirectory (device-*/ucm2/*.conf). Reading
    one directory deep is a choice; doing it silently is the defect."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        up, rel = _repo_with_aport(tmp)
        nested = up / rel / "ucm2"
        nested.mkdir()
        (nested / "HiFi.conf").write_text("x\n")
        git(up, "add", "-A")
        git(up, "commit", "-qm", "nested")

        assert pkg._nested_at(up, "HEAD", rel) == [f"{rel}/ucm2/HiFi.conf"]


def test_every_shape_in_the_table_is_actually_exercised():
    """A fixture that silently stopped producing a symlink would make the
    property test above pass while testing nothing."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        up, rel = _repo_with_aport(tmp)
        modes = {line.split()[3].rsplit("/", 1)[1]: line.split()[0]
                 for line in git(up, "ls-tree", "-r", "HEAD", "--",
                                 f"{rel}/").stdout.splitlines()}
        assert modes["link.post-upgrade"] == "120000", modes
        assert modes["run.sh"] == "100755", modes
        assert modes["plain.patch"] == "100644", modes
        assert b"\x00" in SHAPES["logo.rle"][1], "the binary fixture is text"


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
