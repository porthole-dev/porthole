#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole disk`: pure prune/divergence policy, and the verb around it.

40 G across two divergent pmbootstrap work dirs, nothing pruning either and
nothing reporting what is growing -- see lib/porthole_cmd_disk.py's module
docstring. `prunable()` and `divergence()` are the whole policy, pure, so
these run with no filesystem and no real work dir.

READ-ONLY vs DESTRUCTIVE IS A POSITIONAL ACTION, NOT A FLAG
    `report` (the default) / `prune` / `retire-host` -- fix-round-3: a
    boolean `--prune` could not be granted to an agent without ALSO
    granting `--retire-host` in the same breath, because a permission rule
    is a prefix match and cannot exclude a flag. A positional action word
    fixes that (see lib/porthole_cmd_disk.py's own module docstring).

DESTRUCTIVE PATHS RUN AGAINST PRIVATE TEMPDIRS ONLY
    `prune --yes` and `retire-host --yes --discard-host-workdir` are
    exercised end-to-end here, but always with PORTHOLE_PMB_DIR /
    PORTHOLE_SANDBOX_PMB_DIR pointed at a throwaway
    tempfile.TemporaryDirectory() this test owns -- never at
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


def test_dev_snapshots_are_prunable_even_when_keep_revisions_is_generous():
    """Fix-round-1 concern: keeping 'the 2 newest' of a package must never
    retain a _p snapshot by virtue of keep_revisions being large -- two real
    snapshots from this host (7.2.2_p20260908164336-r0, ...164602-r0)
    blocked every install for eight days before this existed."""
    names = ["linux-x-7.2.2-r29.apk", "linux-x-7.2.2-r30.apk",
            "linux-x-7.2.2-r31.apk",
            "linux-x-7.2.2_p20260908164336-r0.apk",
            "linux-x-7.2.2_p20260908164602-r0.apk"]
    result = disk.prunable(names, keep_revisions=100)
    assert result == ["linux-x-7.2.2_p20260908164336-r0.apk",
                      "linux-x-7.2.2_p20260908164602-r0.apk"], result


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


def test_divergence_orders_versions_naturally_not_lexicographically():
    """Fix-round-1 bug: plain string sort put 0.1-r10 before 0.1-r2 (a real
    row from this host printed `0.1-r0, 0.1-r1, 0.1-r10, ...`), which makes
    a min...max range meaningless. Revisions r0/r1/r2/r10 must come back in
    that numeric order."""
    host_names = [f"device-google-taimen-0.1-r{n}.apk" for n in (0, 1, 2, 10)]
    rows = disk.divergence(host_names, ["device-google-taimen-1-r40.apk"])
    assert rows[0][1] == ["0.1-r0", "0.1-r1", "0.1-r2", "0.1-r10"], rows[0][1]


# ------------------------------------------------------------------ _range --

def test_range_summarizes_count_and_span():
    versions = ["0.1-r0", "0.1-r1", "0.1-r2", "1-r34"]
    assert disk._range(versions, "...") == "4 (0.1-r0 ... 1-r34)"
    assert disk._range(["0.1-r0"]) == "1 (0.1-r0)"
    assert disk._range([]) == "0"


# -------------------------------------------------------------- _parse_apk --

def test_parse_apk_splits_pkgname_version_and_revision():
    assert disk._parse_apk("mesa-26.1.6-r2.apk") == ("mesa", "26.1.6", 2)
    assert (disk._parse_apk("device-google-taimen-0.1-r29.apk")
           == ("device-google-taimen", "0.1", 29))
    assert disk._parse_apk("not-an-apk.txt") is None


# --------------------------------------------------------- _dir_size_bytes --

def test_du_permission_errors_do_not_blank_the_size():
    """Fix-round-1 bug: `du` exits 1 on the FIRST unreadable entry it hits
    while still printing a correct total as its last stdout line. A real
    pmbootstrap work dir always has at least one such entry (a leftover
    chroot bind-mount, a device node), so treating that exit code as
    failure meant every real size came back `?`. Reproduced here with one
    chmod-000 subdirectory -- the same shape `du: cannot read directory
    ...: Permission denied` on this host, exit 1."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-du-") as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "readable").mkdir()
        (tmp / "readable" / "file").write_text("x" * 100)
        blocked = tmp / "blocked"
        blocked.mkdir()
        (blocked / "file").write_text("y" * 50)
        os.chmod(blocked, 0)
        try:
            size, err = disk._dir_size_bytes(tmp)
        finally:
            os.chmod(blocked, 0o755)
        assert size is not None, (
            f"a du permission warning must not blank the size: err={err!r}")
        assert size >= 100, size
        assert err == "", err


def test_a_missing_dir_reports_none_with_no_error():
    size, err = disk._dir_size_bytes(pathlib.Path("/no/such/path/at/all"))
    assert size is None and err == ""


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
        # `>=`, not `==`. `_dir_size_bytes` runs `du -sb`, whose total includes
        # the APPARENT SIZE OF THE DIRECTORIES, and that is a property of the
        # filesystem rather than of porthole: measured 2026-09-18 on the same
        # tree, btrfs answers 5 for these three dirs plus the 5-byte apk, and
        # the floor container's overlayfs answers 53. `== 5` was asserting the
        # developer's filesystem, so `make floor` was red on a healthy tree --
        # brain/laws/a-check-that-fires-on-a-healthy-tree-gets-muted.md.
        #
        # What the row must actually prove is that a size was MEASURED and
        # that our bytes are in it, which is what these two assert. The
        # empty size_error is the positive control: without it a `du` that
        # failed outright would report 0 and still satisfy a `>=` on nothing.
        assert payload["host"]["size_error"] == "", payload
        assert payload["host"]["bytes"] >= 5, payload
        assert payload["sandbox"]["bytes"] >= 5, payload


def test_prune_without_yes_is_refused_and_deletes_nothing():
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "host-pmb", tmp / "sbox-pmb"
        pkgdir = host / "packages" / "edge" / "aarch64"
        pkgdir.mkdir(parents=True)
        (pkgdir / "mesa-26.1.6-r2.apk").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "prune", "--keep-revisions", "0",
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
        rc, out, err = _run("disk", "prune", "--keep-revisions", "2",
                            "--yes", "--json", env=env)
        assert rc == 0, err
        remaining = sorted(p.name for p in pkgdir.glob("*.apk"))
        assert remaining == ["mesa-26.1.6-r4.apk", "mesa-26.1.6-r5.apk"], remaining


def _pmb_shaped(base: pathlib.Path) -> pathlib.Path:
    """A directory that passes _looks_like_pmb_workdir: packages/ and one
    chroot_*."""
    (base / "packages" / "edge" / "aarch64").mkdir(parents=True)
    (base / "chroot_native").mkdir()
    return base


def test_retire_host_alone_only_previews_and_deletes_nothing():
    """`retire-host` alone must PREVIEW (rc 0, nothing touched), the same
    way a bare `porthole flash` previews -- it does not refuse for a
    missing --yes, since --yes alone is not enough to run it either (see
    the next test)."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = _pmb_shaped(tmp / "host-pmb"), tmp / "sbox-pmb"
        (host / "packages" / "edge" / "aarch64" / "marker.apk").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "retire-host", "--json", env=env)
        assert rc == 0, err
        payload = json.loads(out)
        assert payload["would_retire"] == str(host), payload
        assert host.is_dir()


def test_retire_host_yes_without_the_second_flag_is_refused():
    """--yes alone must not be enough -- the flag that names the specific
    loss (--discard-host-workdir) is required too, same rule `porthole
    flash full` applies to --replace-rootfs."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = _pmb_shaped(tmp / "host-pmb"), tmp / "sbox-pmb"

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "retire-host", "--yes", "--json",
                            env=env)
        assert rc != 0, out
        payload = json.loads(out)
        assert "discard-host-workdir" in payload["error"], payload
        assert host.is_dir()


def test_retire_host_refuses_a_dir_that_does_not_look_like_a_pmb_workdir():
    """Both flags present is not enough if the resolved path is not
    pmbootstrap-shaped -- a mis-resolved PORTHOLE_PMB_DIR must not send
    rm -rf at an arbitrary directory (a home directory, say)."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "not-a-pmb-dir", tmp / "sbox-pmb"
        host.mkdir()
        (host / "important-file").write_text("do not delete me")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "retire-host", "--yes",
                            "--discard-host-workdir", "--json", env=env)
        assert rc != 0, out
        payload = json.loads(out)
        assert "does not look like a pmbootstrap work dir" in payload["error"]
        assert host.is_dir() and (host / "important-file").is_file()


def test_retire_host_full_flags_deletes_only_the_host_dir():
    """The sandbox work dir must survive -- retiring the host is never a
    merge, and it must never touch the other work dir."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = _pmb_shaped(tmp / "host-pmb"), _pmb_shaped(tmp / "sbox-pmb")
        (sbox / "marker").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc, out, err = _run("disk", "retire-host", "--yes",
                            "--discard-host-workdir", "--json", env=env)
        assert rc == 0, err
        payload = json.loads(out)
        assert payload["retired"] == str(host), payload
        assert not host.exists(), "host work dir must be gone"
        assert (sbox / "marker").is_file(), "sandbox must be untouched"


def test_report_is_the_default_and_the_explicit_action():
    """`porthole disk` and `porthole disk report` must answer identically --
    the whole reason a bare invocation stays a synonym for the explicit
    action, rather than requiring `report` to be typed."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = _pmb_shaped(tmp / "host-pmb"), tmp / "sbox-pmb"

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))
        rc1, out1, err1 = _run("disk", "--json", env=env)
        rc2, out2, err2 = _run("disk", "report", "--json", env=env)
        assert rc1 == rc2 == 0, (err1, err2)
        p1, p2 = json.loads(out1), json.loads(out2)
        # free_bytes is real free space on /tmp, sampled twice a process
        # apart -- under a parallel test run other workers are writing to
        # /tmp at the same moment, so it is the one field allowed to differ
        # between two calls a heartbeat apart. Everything this dispatch
        # actually computes (sizes, prunable, divergence) must match.
        for payload in (p1, p2):
            payload["host"].pop("free_bytes", None)
        assert p1 == p2


def test_an_unknown_action_is_a_usage_error():
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        env = _env(pathlib.Path(tmp) / "home")
        rc, out, err = _run("disk", "bogus", env=env)
        assert rc == 64, (rc, out, err)


def test_text_output_summarizes_divergence_verbose_shows_every_revision():
    """Fix-round-1: default text output must not dump a 61-revision wall
    (one real line from this host: `host 0.1-r0, 0.1-r1, 0.1-r10, ...
    (61 revisions) ... vs sandbox 1-r34, ...`). --json always carries the
    full list regardless."""
    with tempfile.TemporaryDirectory(prefix="porthole-disk-cli-") as tmp:
        tmp = pathlib.Path(tmp)
        host, sbox = tmp / "host-pmb", tmp / "sbox-pmb"
        pkgdir_h = host / "packages" / "edge" / "aarch64"
        pkgdir_s = sbox / "packages" / "edge" / "aarch64"
        pkgdir_h.mkdir(parents=True)
        pkgdir_s.mkdir(parents=True)
        for n in range(0, 8):
            (pkgdir_h / f"device-google-taimen-0.1-r{n}.apk").write_text("x")
        (pkgdir_s / "device-google-taimen-1-r40.apk").write_text("x")

        env = _env(tmp / "home", PORTHOLE_PMB_DIR=str(host),
                  PORTHOLE_SANDBOX_PMB_DIR=str(sbox))

        rc, out, err = _run("disk", "--no-color", env=env)
        assert rc == 0, err
        assert "0.1-r0" in out and "0.1-r7" in out
        assert "0.1-r1," not in out, (
            f"default output must summarise, not list every revision:\n{out}")

        rc, out, err = _run("disk", "--no-color", "--verbose", env=env)
        assert rc == 0, err
        assert "0.1-r0, 0.1-r1," in out, (
            f"--verbose must show the full comma-separated list:\n{out}")

        rc, out, err = _run("disk", "--json", env=env)
        payload = json.loads(out)
        assert len(payload["divergence"][0]["host"]) == 8, payload


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
