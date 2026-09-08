#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole log`: rotation policy, and the verb around it.

255+ unrotated build logs, 131 MB, going back to 2026-08-29, and no verb
read them -- see lib/porthole_cmd_log.py's module docstring. `keep()` is the
whole policy, pure, so these run with no filesystem and no clock but the one
handed in.

HERMETIC
    The CLI smoke test runs with HOME redirected AND PORTHOLE_KERNEL_PKG /
    PORTHOLE_DEVICE unset -- this host exports both, and
    ~/.config/porthole/config.env sets PORTHOLE_DEVICE on disk too, so a
    fixture that does not defeat all three would only prove the verb works
    on this one desk.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

import porthole_cmd_log as log  # noqa: E402
from porthole_cli import Bail  # noqa: E402


# --------------------------------------------------------------- keep() --

def test_rotation_keeps_the_recent_ones_and_drops_the_rest():
    """255 logs, 131 MB, back to 2026-08-29 -- nothing ever collected them."""
    names = [f"build-auto-2026090{d}-1200{i:02d}.log"
             for d in range(1, 9) for i in range(40)]
    kept, dropped = log.keep(names, max_count=50)
    assert len(kept) == 50, len(kept)
    assert len(dropped) == len(names) - 50, len(dropped)


def test_the_newest_log_is_never_dropped():
    names = ["build-auto-20260901-120000.log", "build-auto-20260908-120000.log"]
    kept, _ = log.keep(names, max_count=1)
    assert kept == ["build-auto-20260908-120000.log"], kept


def test_a_log_the_running_build_is_writing_is_never_dropped():
    """Rotating out the file a live build is appending to loses the only
    record of the thing you are watching."""
    names = ["build-auto-20260901-120000.log", "build-fast-20260908-120000.log"]
    kept, dropped = log.keep(names, max_count=1,
                             active="build-auto-20260901-120000.log")
    assert "build-auto-20260901-120000.log" in kept
    assert "build-auto-20260901-120000.log" not in dropped


def test_max_age_days_drops_a_log_even_within_max_count():
    """The count cap alone is not the whole policy -- a handful of logs that
    are all ancient must not survive just because there are fewer than 50 of
    them."""
    names = ["build-auto-20260101-000000.log", "build-auto-20260908-000000.log"]
    now = datetime(2026, 9, 8)
    kept, dropped = log.keep(names, max_count=50, max_age_days=14, now=now)
    assert kept == ["build-auto-20260908-000000.log"], kept
    assert dropped == ["build-auto-20260101-000000.log"], dropped


def test_a_name_with_no_parseable_stamp_does_not_crash_and_is_not_exempt():
    """A detached spawn log (`build-fast-detached.log`) has no
    YYYYMMDD-HHMMSS to rank -- it must lose ties for a max_count slot, not
    silently escape rotation forever."""
    names = ["build-fast-detached.log", "build-auto-20260908-000000.log"]
    kept, dropped = log.keep(names, max_count=1)
    assert kept == ["build-auto-20260908-000000.log"], kept
    assert dropped == ["build-fast-detached.log"], dropped


# ---------------------------------------------------------- rung_of() --

def test_rung_of_extracts_the_slug_between_prefix_and_stamp():
    assert log.rung_of("build-auto-20260908-120000.log") == "auto"
    assert (log.rung_of("build-pkg-webkit2gtk-6.0-20260908-120000.log")
           == "pkg-webkit2gtk-6.0")
    assert log.rung_of("build-fast-detached.log") == ""


# ------------------------------------------------------ parse_duration() --

def test_parse_duration_understands_every_unit():
    assert log.parse_duration("30s") == 30.0
    assert log.parse_duration("90m") == 5400.0
    assert log.parse_duration("6h") == 21600.0
    assert log.parse_duration("2d") == 172800.0


def test_parse_duration_rejects_garbage():
    try:
        log.parse_duration("banana")
    except Bail as exc:
        assert "banana" in exc.message
    else:
        raise AssertionError("expected Bail on an unparseable duration")


# ------------------------------------------------------------ _active_log --

def _write(path, text=""):
    path.write_text(text)


def test_active_log_is_the_newest_log_only_while_a_build_is_running():
    with tempfile.TemporaryDirectory(prefix="porthole-log-test-") as tmp:
        rundir = pathlib.Path(tmp)
        _write(rundir / "build-auto-20260901-000000.log")
        _write(rundir / "build-fast-20260908-000000.log")
        os.utime(rundir / "build-auto-20260901-000000.log", (1, 1))
        os.utime(rundir / "build-fast-20260908-000000.log", (2, 2))

        (rundir / "build-status.json").write_text(json.dumps(
            {"state": "running", "pid": os.getpid()}))
        assert log._active_log(rundir) == "build-fast-20260908-000000.log"

        (rundir / "build-status.json").write_text(json.dumps(
            {"state": "done", "pid": os.getpid()}))
        assert log._active_log(rundir) == ""


def test_active_log_is_empty_with_no_status_file():
    with tempfile.TemporaryDirectory(prefix="porthole-log-test-") as tmp:
        assert log._active_log(pathlib.Path(tmp)) == ""


# ------------------------------------------------------------------ CLI --

def test_bare_invocation_lists_and_touches_nothing():
    """READ-ONLY BY DEFAULT: a bare `porthole log` must not delete a file,
    even one well past the keep window."""
    with tempfile.TemporaryDirectory(prefix="porthole-log-cli-") as home:
        rundir = pathlib.Path(home) / ".run"
        rundir.mkdir()
        stale = rundir / "build-auto-20200101-000000.log"
        stale.write_text("old\n")

        env = dict(os.environ)
        for key in ("PORTHOLE_KERNEL_PKG", "PORTHOLE_DEVICE"):
            env.pop(key, None)
        env.update({"HOME": home, "XDG_CONFIG_HOME": str(pathlib.Path(home) / "xdg"),
                   "PORTHOLE_ROOT": home, "PORTHOLE_RUNDIR": str(rundir),
                   "NO_COLOR": "1", "PATH": os.environ["PATH"]})

        proc = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "porthole"), "log", "--json"],
            capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert any(row["name"] == stale.name for row in payload["logs"]), payload
        assert stale.is_file(), "a bare invocation must never delete a log"


def test_prune_without_yes_is_refused_and_json_on_failure():
    with tempfile.TemporaryDirectory(prefix="porthole-log-cli-") as home:
        rundir = pathlib.Path(home) / ".run"
        rundir.mkdir()
        (rundir / "build-auto-20200101-000000.log").write_text("old\n")

        env = dict(os.environ)
        for key in ("PORTHOLE_KERNEL_PKG", "PORTHOLE_DEVICE"):
            env.pop(key, None)
        env.update({"HOME": home, "XDG_CONFIG_HOME": str(pathlib.Path(home) / "xdg"),
                   "PORTHOLE_ROOT": home, "PORTHOLE_RUNDIR": str(rundir),
                   "NO_COLOR": "1", "PATH": os.environ["PATH"]})

        proc = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "porthole"), "log", "--prune",
             "--json"],
            capture_output=True, text=True, env=env)
        assert proc.returncode != 0, proc.stdout
        payload = json.loads(proc.stdout)
        assert payload["error"], payload
        assert (rundir / "build-auto-20200101-000000.log").is_file()


def test_hermetic_no_home_no_env_still_answers():
    """HOME points at a directory that does not exist, and the two env vars
    this host happens to export are unset too -- the verb must still return
    a valid document, not crash looking for a profile it does not need."""
    env = {"PATH": os.environ["PATH"], "HOME": "/tmp/nonexistent-home-xyz",
          "XDG_CONFIG_HOME": "/tmp/nonexistent-home-xyz/.config",
          "PORTHOLE_ROOT": str(ROOT), "NO_COLOR": "1"}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "porthole"), "log", "--json"],
        capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "logs" in payload, payload


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
