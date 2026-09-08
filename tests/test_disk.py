#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole disk`: pure prune/divergence policy, and the verb around it.

40 G across two divergent pmbootstrap work dirs, nothing pruning either and
nothing reporting what is growing -- see lib/porthole_cmd_disk.py's module
docstring. `prunable()` and `divergence()` are the whole policy, pure, so
these run with no filesystem and no real work dir.

DESTRUCTIVE PATHS RUN AGAINST PRIVATE TEMPDIRS ONLY
    `--prune --yes` and `--retire-host --yes` are exercised end-to-end here,
    but always with PORTHOLE_PMB_DIR / PORTHOLE_SANDBOX_PMB_DIR pointed at a
    throwaway tempfile.TemporaryDirectory() this test owns -- never at
    /tmp/nonexistent-home-xyz (a fixture shared with other tests in this
    live worktree) and never at a real host path.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

import porthole_cmd_disk as disk  # noqa: E402


# ------------------------------------------------------------- prunable() --

def test_old_revisions_of_the_same_package_are_prunable():
    """mesa r2 through r14 and device r34 through r40 were all present; a
    handful matter."""
    names = [f"mesa-26.1.6-r{n}.apk" for n in range(2, 15)]
    assert len(disk.prunable(names, keep_revisions=2)) == 11
    assert "mesa-26.1.6-r14.apk" not in disk.prunable(names, keep_revisions=2)


def test_a_dev_snapshot_is_prunable_whatever_its_age():
    """_p sorts above -rNN, so one left behind blocks every install."""
    names = ["linux-x-7.2.2-r31.apk", "linux-x-7.2.2_p20260908083444-r0.apk"]
    assert disk.prunable(names) == ["linux-x-7.2.2_p20260908083444-r0.apk"]


def test_keep_revisions_is_honoured():
    names = [f"mesa-26.1.6-r{n}.apk" for n in range(2, 15)]
    assert len(disk.prunable(names, keep_revisions=5)) == 8
    kept = set(names) - set(disk.prunable(names, keep_revisions=5))
    assert kept == {f"mesa-26.1.6-r{n}.apk" for n in range(10, 15)}, kept


def test_names_that_do_not_fit_the_apk_shape_are_left_alone():
    """A parser that guesses on a name it cannot fully decompose is how a
    real apk gets deleted. Not our shape -> never prunable."""
    assert disk.prunable(["README.txt", "not-an-apk"]) == []


# ------------------------------------------------------------ divergence() --

def test_the_two_work_dirs_are_reported_as_divergent_never_merged():
    """Host had device-google-taimen-0.1-rNN, sandbox -1-rNN. Merging them is
    a decision about which build wins and porthole must not make it."""
    rows = disk.divergence(["device-google-taimen-0.1-r29.apk"],
                           ["device-google-taimen-1-r40.apk"])
    assert rows and rows[0][0] == "device-google-taimen"


def test_identical_builds_in_both_work_dirs_are_not_divergence():
    names = ["mesa-26.1.6-r2.apk"]
    assert disk.divergence(names, list(names)) == []


def test_a_package_only_present_on_one_side_is_not_divergence():
    """Divergence is about the SAME package built differently, not about
    what one work dir has and the other does not."""
    rows = disk.divergence(["mesa-26.1.6-r2.apk"], ["phoc-0.56.0-r1.apk"])
    assert rows == []


# -------------------------------------------------------------- _parse_apk --

def test_parse_apk_splits_pkgname_version_and_revision():
    assert disk._parse_apk("mesa-26.1.6-r2.apk") == ("mesa", "26.1.6", 2)
    assert (disk._parse_apk("device-google-taimen-0.1-r29.apk")
           == ("device-google-taimen", "0.1", 29))
    assert disk._parse_apk("not-an-apk.txt") is None


# ------------------------------------------------------------------ CLI --

def _env(home, **extra):
    env = {"PATH": os.environ["PATH"], "HOME": str(home),
          "XDG_CONFIG_HOME": str(home / ".config"),
          "PORTHOLE_ROOT": str(ROOT), "NO_COLOR": "1"}
    env.update(extra)
    return env


def _run(*argv, env):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "porthole"), *argv],
        capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def test_bare_invocation_reports_and_deletes_nothing():
    """READ-ONLY BY DEFAULT: a bare `porthole disk` must not touch a file
    that would otherwise be prunable."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "host-pmb", tmp / "sbox-pmb"
        pkgdir = host / "packages" / "edge" / "aarch64"
        pkgdir.mkdir(parents=True)
        old = pkgdir / "mesa-26.1.6-r2.apk"
        old.write_text("x")
        (pkgdir / "mesa-26.1.6-r3.apk").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "--keep-revisions", "1", "--json", env=env)
        assert rc == 0, err
        payload = json.loads(out)
        assert old.name in payload["host"]["prunable"], payload
        assert old.is_file(), "a bare invocation must never delete an apk"


def test_divergence_and_sizes_are_reported():
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "host-pmb", tmp / "sbox-pmb"
        for base, apk in ((host, "device-google-taimen-0.1-r29.apk"),
                          (sbox, "device-google-taimen-1-r40.apk")):
            pkgdir = base / "packages" / "edge" / "aarch64"
            pkgdir.mkdir(parents=True)
            (pkgdir / apk).write_text("x" * 5)

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "--json", env=env)
        assert rc == 0, err
        payload = json.loads(out)
        assert payload["divergence"], payload
        assert payload["divergence"][0]["package"] == "device-google-taimen"
        assert payload["host"]["bytes"] == 5, payload
        assert payload["sandbox"]["bytes"] == 5, payload


def test_prune_without_yes_is_refused_and_deletes_nothing():
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "host-pmb", tmp / "sbox-pmb"
        pkgdir = host / "packages" / "edge" / "aarch64"
        pkgdir.mkdir(parents=True)
        (pkgdir / "mesa-26.1.6-r2.apk").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "--keep-revisions", "0", "--prune",
                            "--json", env=env)
        assert rc != 0, out
        payload = json.loads(out)
        assert payload["error"], payload
        assert (pkgdir / "mesa-26.1.6-r2.apk").is_file()


def test_prune_yes_deletes_only_the_prunable_apks():
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "host-pmb", tmp / "sbox-pmb"
        pkgdir = host / "packages" / "edge" / "aarch64"
        pkgdir.mkdir(parents=True)
        for n in range(2, 6):
            (pkgdir / f"mesa-26.1.6-r{n}.apk").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "--keep-revisions", "2", "--prune",
                            "--yes", "--json", env=env)
        assert rc == 0, err
        remaining = sorted(p.name for p in pkgdir.glob("*.apk"))
        assert remaining == ["mesa-26.1.6-r4.apk", "mesa-26.1.6-r5.apk"], remaining


def test_retire_host_without_yes_is_refused():
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "host-pmb", tmp / "sbox-pmb"
        host.mkdir()
        (host / "marker").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "--retire-host", "--json", env=env)
        assert rc != 0, out
        assert host.is_dir() and (host / "marker").is_file()


def test_retire_host_yes_deletes_only_the_host_dir():
    """The sandbox work dir must survive -- retiring the host is never a
    merge, and it must never touch the other work dir."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "host-pmb", tmp / "sbox-pmb"
        host.mkdir()
        sbox.mkdir()
        (host / "marker").write_text("x")
        (sbox / "marker").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "--retire-host", "--yes", "--json",
                            env=env)
        assert rc == 0, err
        payload = json.loads(out)
        assert payload["retired"] == str(host), payload
        assert not host.exists(), "host work dir must be gone"
        assert (sbox / "marker").is_file(), "sandbox must be untouched"


def test_hermetic_no_home_no_env_still_answers():
    """HOME points at a directory that does not exist, and the two env vars
    this host happens to export are unset too -- the verb must still return
    a valid document, not crash looking for a device profile it does not
    need."""
    env = {"PATH": os.environ["PATH"], "HOME": "/tmp/nonexistent-home-xyz",
          "XDG_CONFIG_HOME": "/tmp/nonexistent-home-xyz/.config",
          "PORTHOLE_ROOT": str(ROOT), "NO_COLOR": "1"}
    rc, out, err = _run("disk", "--json", env=env)
    assert rc == 0, err
    payload = json.loads(out)
    assert "host" in payload and "sandbox" in payload, payload


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
