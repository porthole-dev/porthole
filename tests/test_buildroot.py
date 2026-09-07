#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The buildroot mutex.

One chroot per arch, and abuild wipes $srcdir before it unpacks. Two sessions
have now paid for the absence of this lock -- a webkit build 37 minutes in on
taimen, and a kernel build on the redfin port killed by a parallel `checksum`.
Both failures named the compiler.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

import porthole_buildroot as buildroot  # noqa: E402


# ---------------------------------------------------------- the buildroot --
#
# One workspace, one buildroot per arch, and abuild wipes $srcdir before it
# unpacks. The cost of getting this wrong is measured: a webkit build died 37
# minutes in with `clang++: no such file or directory: TextMetrics.idl`,
# because a second build had replaced the source tree underneath it.

def test_a_second_build_is_refused_while_one_holds_the_buildroot():
    with tempfile.TemporaryDirectory() as d:
        with buildroot.hold(d, "webkit2gtk-6.0"):
            assert not buildroot.is_free(d)
            try:
                with buildroot.hold(d, "gst-plugins-good"):
                    assert False, "the second build was allowed to start"
            except Exception as exc:
                assert "busy" in str(exc)


def test_the_refusal_names_who_is_building_rather_than_just_saying_busy():
    """"Someone has it" sends you looking. The holder file is what turns that
    into an answer -- the same reason ph-device.sh records one."""
    with tempfile.TemporaryDirectory() as d:
        with buildroot.hold(d, "webkit2gtk-6.0"):
            assert "webkit2gtk-6.0" in buildroot.lock_holder(d)


def test_the_lock_is_released_and_the_holder_cleared_afterwards():
    with tempfile.TemporaryDirectory() as d:
        with buildroot.hold(d, "phoc"):
            pass
        assert buildroot.lock_holder(d) == ""
        assert buildroot.is_free(d)


def test_waiting_gives_up_with_the_retryable_code_not_a_plain_failure():
    """75 means "retry"; 1 means "the build failed". An agent that cannot
    tell them apart reports a busy buildroot as a broken package."""
    from porthole_cli import EX_LOCK

    with tempfile.TemporaryDirectory() as d:
        with buildroot.hold(d, "webkit2gtk-6.0"):
            try:
                with buildroot.hold(d, "phoc", wait=0.2):
                    assert False, "should not have acquired"
            except Exception as exc:
                assert getattr(exc, "code", None) == EX_LOCK, exc


def test_a_build_started_outside_the_verb_is_still_detected():
    """The flock binds only callers who take it. `sandbox shell --command
    pmbootstrap build ...` takes nothing, and that is exactly how the webkit
    build that got destroyed had been started."""
    ps = ("  135565 /usr/bin/python3 /usr/bin/pmbootstrap --as-root --config "
          "/pmb/pmbootstrap_v3.cfg --details-to-stdout build --lax "
          "webkit2gtk-6.0 --arch aarch64\n"
          "  1 /sbin/init\n")
    assert buildroot.foreign_build(ps) == "webkit2gtk-6.0"


def test_an_idle_container_reports_no_foreign_build():
    assert buildroot.foreign_build("  1 /sbin/init\n  2 /bin/bash\n") == ""


def test_the_guard_cannot_match_its_own_probe():
    """A `pgrep -f "pmbootstrap.*build"` guard in a shell one-liner puts the
    pattern into its own command line and matches ITSELF -- an agent waiting
    that way waits forever on a buildroot that is free. Observed in a real
    session. Reading ps and filtering here cannot do that, but a pgrep line
    that somehow appears must still not count."""
    ps = "  99 sh -c pgrep -af 'pmbootstrap.*build'\n"
    assert buildroot.foreign_build(ps) == ""



def test_checksum_and_build_contend_for_the_same_lock():
    """The redfin loss: `pmbootstrap checksum` removed the buildroot out from
    under an active kernel build. The two commands look independent, so the
    mutex has to be shared rather than per-verb -- a lock only the build verb
    takes is not a lock."""
    with tempfile.TemporaryDirectory() as d:
        with buildroot.hold(d, "aports checksum"):
            assert not buildroot.is_free(d)
            assert "checksum" in buildroot.lock_holder(d)


def test_the_holder_names_the_operation_not_only_the_package():
    with tempfile.TemporaryDirectory() as d:
        with buildroot.hold(d, "aports pkgrel_bump"):
            assert "pkgrel_bump" in buildroot.lock_holder(d)


def test_a_dead_holder_plus_a_live_build_refuses_rather_than_deleting_it():
    """2026-09-01, measured: a webkit build's porthole run was killed and the
    build kept going inside the workspace -- `podman exec` runs it
    server-side. flock is released by the kernel when its holder dies, so the
    mutex read FREE with a nine-thousand-object build in the buildroot, and
    the next `aports checksum` would have deleted its source tree. The leaked
    sidecar plus the workspace's process list is what catches it."""
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / ".porthole-buildroot.lock.holder").write_text(
            "webkit2gtk-6.0 pid=999999999 since=20:36:15\n")
        assert buildroot.is_free(d), "flock really is free -- that is the trap"
        assert "webkit2gtk-6.0" in buildroot.abandoned(d)
        try:
            with buildroot.hold(d, "aports checksum",
                                probe=lambda: "webkit2gtk-6.0"):
                raise AssertionError("must refuse: that build is still alive")
        except buildroot.Bail as exc:
            assert exc.code == buildroot.EX_LOCK, exc.code
            assert "webkit2gtk-6.0" in exc.message
            assert "outlived its tracker" in exc.hint


def test_a_leaked_holder_from_a_finished_build_does_not_wedge_the_next_one():
    """The other half, and the reason this is a hint and never a refusal on
    its own: flock was chosen over a lockfile precisely so a crashed build
    cannot wedge everyone until a human notices. A sidecar whose build is
    provably over is litter -- clear it and proceed."""
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / ".porthole-buildroot.lock.holder").write_text(
            "phoc pid=999999999 since=11:02:03\n")
        with buildroot.hold(d, "aports checksum", probe=lambda: ""):
            assert "phoc" not in buildroot.lock_holder(d), \
                "the dead holder must be gone, not inherited"
        assert buildroot.is_free(d)


def test_a_live_holder_is_never_called_abandoned():
    with tempfile.TemporaryDirectory() as d:
        with buildroot.hold(d, "phoc"):
            assert buildroot.abandoned(d) == ""
            assert buildroot.holder_pid(d) == os.getpid()


def main():
    return _runner.run(globals())


# ------------------------------------------------- naming an untracked build --
#
# Reported 2026-09-06: the status line showed no build while a webkit2gtk-6.0
# build had been running for two hours, and `pkg watch` showed it. The tracked
# snapshot said `done` -- a PREVIOUS build had finished and written it -- and
# every consumer asks the snapshot, so a build nobody tracked is invisible.
# `pkg watch` saw it only because it separately shells out to a podman `ps`,
# which the status line may not do at a two-second refresh.
#
# The buildroot names the build for free: pmbootstrap stages the APKBUILD it is
# building into the chroot, so one small file read says which package, and its
# mtime says when the build began -- a field the log alone reports as None.

def test_the_staged_apkbuild_names_the_running_build():
    work = pathlib.Path(tempfile.mkdtemp(prefix="porthole-staged-"))
    build = work / "chroot_buildroot_aarch64" / "home" / "pmos" / "build"
    build.mkdir(parents=True)
    (build / "APKBUILD").write_text(
        "# Contributor: someone\npkgname=webkit2gtk-6.0\npkgver=2.52.6\n")
    name, started = buildroot.staged_build_name(work)
    assert name == "webkit2gtk-6.0", name
    assert started is not None and started > 0, started


def test_no_buildroot_names_nothing():
    """Absence must be None, never a guess. A wrong package name in the chrome
    is worse than no row: it attributes somebody else's four-hour build to the
    thing you are working on."""
    work = pathlib.Path(tempfile.mkdtemp(prefix="porthole-staged-"))
    assert buildroot.staged_build_name(work) == (None, None)


def test_the_newest_buildroot_wins():
    """One workspace can hold a buildroot per architecture. The one written
    most recently is the one a fresh log belongs to."""
    work = pathlib.Path(tempfile.mkdtemp(prefix="porthole-staged-"))
    for arch, name, when in (("armv7", "older-pkg", 1000), ("aarch64", "newer-pkg", 2000)):
        d = work / f"chroot_buildroot_{arch}" / "home" / "pmos" / "build"
        d.mkdir(parents=True)
        (d / "APKBUILD").write_text(f"pkgname={name}\n")
        os.utime(d / "APKBUILD", (when, when))
    name, started = buildroot.staged_build_name(work)
    assert name == "newer-pkg", name
    assert started == 2000, started


def test_every_chroot_owning_subcommand_counts_as_busy():
    """`build` alone was the whole list, and that is how a running
    `pmbootstrap install` read as an idle buildroot: `porthole build clean`
    unstacked /mnt/linux underneath a live install and nothing objected.
    `install` runs mkfs and populates the rootfs chroot, `zap` deletes chroots
    outright, `checksum` is the command that destroyed the redfin kernel
    build."""
    for sub in ("install", "export", "zap", "checksum", "flasher", "chroot"):
        ps = (f"  9 /usr/bin/python3 /usr/bin/pmbootstrap --as-root --config "
              f"/pmb/pmbootstrap_v3.cfg {sub} --password hunter2\n")
        assert buildroot.foreign_build(ps) == f"pmbootstrap {sub}", sub


def test_a_read_only_pmbootstrap_is_not_busy():
    """`status`, `log`, `config` and `pull` are cheap and read-only, and
    `redirect_for` leaves them available on purpose. A guard that fires on
    `pmbootstrap log` is a guard people route around."""
    for sub in ("status", "log", "config", "pull"):
        ps = f"  9 /usr/bin/python3 /usr/bin/pmbootstrap {sub}\n"
        assert buildroot.foreign_build(ps) == "", sub


def test_the_subcommand_is_found_past_the_global_options():
    """`--config <path>` takes a separate value, and reading a fixed position
    after `pmbootstrap` would take that path for the subcommand."""
    ps = ("  9 /usr/bin/python3 /usr/bin/pmbootstrap --as-root --config "
          "/pmb/pmbootstrap_v3.cfg --details-to-stdout build --lax mypkg\n")
    assert buildroot.foreign_build(ps) == "mypkg", buildroot.foreign_build(ps)


def test_a_pmbootstrap_with_no_subcommand_is_not_busy():
    assert buildroot.foreign_build("  9 /usr/bin/pmbootstrap --help\n") == ""
    assert buildroot.foreign_build("  9 /usr/bin/pmbootstrap\n") == ""


def test_a_foreign_build_is_named_after_the_package_not_the_arch_value():
    ps = ("  9 /usr/bin/python3 /usr/bin/pmbootstrap --as-root build --lax "
          "--src=/work/webkit-src/webkitgtk-2.52.6 --arch aarch64 webkit2gtk-6.0\n")
    assert buildroot.foreign_build(ps) == "webkit2gtk-6.0"
    ps = "  9 pmbootstrap build --arch aarch64 --src /work/x webkit2gtk-6.0\n"
    assert buildroot.foreign_build(ps) == "webkit2gtk-6.0"


if __name__ == "__main__":
    sys.exit(main())
