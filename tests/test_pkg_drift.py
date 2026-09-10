#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole pkg drift` -- has upstream moved past a fork we carry?"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole                                              # noqa: E402
import porthole_pmaports as pmap                              # noqa: E402
import porthole_cmd_pkg as pkg                               # noqa: E402
from porthole_cli import EX_FAIL                              # noqa: E402

PORTHOLE = str(ROOT / "bin" / "porthole")


class Skip(Exception):
    """This test could not run here -- reported as `skip`, never as a pass."""


# The two tests below drive the REAL google-taimen profile against a real
# pmaports checkout. CI's smoke job runs from a fresh clone with an empty
# HOME and has neither, so they must skip there rather than fail. Resolved
# once and handed to the subprocess as PORTHOLE_PMAPORTS, so the guard and
# the thing it guards look at the same checkout (the trap test_cli_rules.py
# documents at length).
_found = pmap.find_pmaports(porthole.load_config(root=ROOT))
PMAPORTS = str(_found) if _found else ""


def _drift_json():
    if not PMAPORTS:
        raise Skip("no pmaports checkout on this host")
    got = subprocess.run(
        [PORTHOLE, "-d", "google-taimen", "pkg", "drift", "--json"],
        capture_output=True, text=True,
        env={**os.environ, "PORTHOLE_PMAPORTS": PMAPORTS})
    return got


def test_drift_json_reports_a_verdict_per_carried_fork():
    got = _drift_json()
    # Exit 0 (nothing at risk) or 1 (something is) are both real answers;
    # anything else is the command failing to run.
    assert got.returncode in (0, 1), got.stderr
    payload = json.loads(got.stdout)
    assert "aports" in payload, payload
    for name, row in payload["aports"].items():
        assert row["verdict"] in (
            "safe", "loses", "at-risk", "unknown", "unresolved"), row


def test_entries_we_own_outright_are_not_reported_as_drifting():
    got = _drift_json()
    payload = json.loads(got.stdout)
    # device-google-taimen is ours, not a fork of anything upstream.
    row = payload["aports"].get("device-google-taimen")
    assert row is None or row["verdict"] == "unknown", row


# --------------------------------------------- unresolved vs. unknown --
#
# These two build a synthetic root/pmaports/aports_upstream in a tempdir and
# call `_drift` in-process, rather than through the real google-taimen
# profile -- the failure this covers (a manifest `upstream:` path that does
# not exist on disk) is exactly the bug that shipped once already (round 1:
# phoc/libcamera declared `main/...` while actually living in `community/`)
# and a real profile that is currently correct cannot exercise it.

class _FakeOut:
    """Records what render() would print, instead of printing it."""

    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(text)

    def kv(self, key, value, width=0, note=""):
        self.lines.append(f"{key}: {value}")

    def blank(self):
        self.lines.append("")

    def warn(self, text, stream=None):
        self.lines.append(f"warning: {text}")

    def hint(self, text, note="", stream=None):
        self.lines.append(f"hint: {text}")


class _FakeCtx:
    """Just enough of porthole_cli.Ctx for `_drift`: .root, .cfg, .out, .emit."""

    def __init__(self, root, cfg):
        self.root, self.cfg, self.out = root, cfg, _FakeOut()
        self.captured = None

    def emit(self, payload, render=None):
        self.captured = payload
        if render:
            render()
        return 0


def _synthetic_ctx(tmp: pathlib.Path):
    """A pmaports + aports_upstream pair, and a manifest with one fork whose
    `upstream:` path does not exist and one entry owned outright."""
    pm = tmp / "cache_git" / "pmaports"
    up = tmp / "cache_git" / "aports_upstream"
    (pm / "device").mkdir(parents=True)                  # pmaports marker
    (up / "main").mkdir(parents=True)                    # aports_upstream marker
    (pm / "temp" / "broken-fork").mkdir(parents=True)
    (pm / "temp" / "broken-fork" / "APKBUILD").write_text(
        "pkgname=broken-fork\npkgver=1.0\npkgrel=5\n")

    # A fork that RESOLVES for real, and upstream HAS moved past it -- the
    # positive control the design (§13) asks for: "a test that cannot fail
    # proves nothing... the drift suite must include a fork that IS
    # outranked and assert the alarm fires". Without this, the only verdicts
    # this suite could ever produce were `unresolved` and `unknown`, and the
    # whole APKBUILD-read -> verdict() -> patches -> bad -> EX_FAIL chain
    # went untested.
    (pm / "temp" / "outranked-fork").mkdir(parents=True)
    (pm / "temp" / "outranked-fork" / "APKBUILD").write_text(
        "pkgname=outranked-fork\npkgver=1.0\npkgrel=5\n")
    (up / "main" / "outranked-fork").mkdir(parents=True)
    (up / "main" / "outranked-fork" / "APKBUILD").write_text(
        "pkgname=outranked-fork\npkgver=2.0\npkgrel=0\n")

    profile = tmp / "profiles" / "test-device"
    profile.mkdir(parents=True)
    (profile / "aports.conf").write_text(
        "broken-fork\n"
        "  upstream: main/nonexistent-nowhere\n"
        "  tier:     required\n"
        "  why:      fixture for the unresolved verdict\n"
        "  forked:   1.0-r0\n"
        "  commit:   unknown\n"
        "\n"
        "outranked-fork\n"
        "  upstream: main/outranked-fork\n"
        "  tier:     required\n"
        "  why:      fixture for the at-risk verdict\n"
        "  forked:   1.0-r5\n"
        "  commit:   unknown\n"
        "\n"
        "ours-outright\n"
        "  upstream: (none -- ours, not a fork)\n"
        "  tier:     required\n"
        "  why:      fixture, owned outright\n"
        "  forked:   n/a\n"
        "  commit:   n/a\n")

    return _FakeCtx(tmp, {"PORTHOLE_DEVICE": "test-device",
                          "PORTHOLE_PMAPORTS": str(pm)})


def test_a_wrong_upstream_path_is_unresolved_and_fails_a_required_fork():
    with tempfile.TemporaryDirectory() as d:
        ctx = _synthetic_ctx(pathlib.Path(d))
        rc = pkg._drift(ctx, None)

        row = ctx.captured["aports"]["broken-fork"]
        assert row["verdict"] == "unresolved", row
        assert "broken-fork" in ctx.captured["at_risk"]
        assert rc == EX_FAIL

        # Visible in render(), not silently dropped like "unknown" is.
        assert any("broken-fork" in ln and "COULD NOT BE CHECKED" in ln
                   for ln in ctx.out.lines), ctx.out.lines
        # The all-clear sentence must not appear when something couldn't be
        # checked at all.
        assert not any("every carried fork still outranks upstream" in ln
                      for ln in ctx.out.lines), ctx.out.lines


def test_owned_outright_stays_unknown_and_never_fails_the_exit_code():
    with tempfile.TemporaryDirectory() as d:
        ctx = _synthetic_ctx(pathlib.Path(d))
        pkg._drift(ctx, None)

        row = ctx.captured["aports"]["ours-outright"]
        assert row["verdict"] == "unknown", row
        assert "ours-outright" not in ctx.captured["at_risk"]


def test_a_fork_upstream_moved_past_is_at_risk_and_the_alarm_fires():
    """The positive control: a fork that DOES resolve, and upstream HAS
    moved past it. Drives the whole chain the other two tests above cannot
    reach -- APKBUILD-read -> verdict() -> patches -> bad -> EX_FAIL."""
    with tempfile.TemporaryDirectory() as d:
        ctx = _synthetic_ctx(pathlib.Path(d))
        rc = pkg._drift(ctx, None)

        row = ctx.captured["aports"]["outranked-fork"]
        assert row["verdict"] == "at-risk", row
        assert row["ours"] == "1.0-r5", row
        assert row["upstream"] == "2.0-r0", row
        assert "outranked-fork" in ctx.captured["at_risk"]
        assert rc == EX_FAIL

        assert any("outranked-fork" in ln and "AT-RISK" in ln
                   for ln in ctx.out.lines), ctx.out.lines


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
