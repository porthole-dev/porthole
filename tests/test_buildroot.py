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
    into an answer -- the same reason tk-device.sh records one."""
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
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())


def test_a_foreign_build_is_named_after_the_package_not_the_arch_value():
    ps = ("  9 /usr/bin/python3 /usr/bin/pmbootstrap --as-root build --lax "
          "--src=/work/webkit-src/webkitgtk-2.52.6 --arch aarch64 webkit2gtk-6.0\n")
    assert buildroot.foreign_build(ps) == "webkit2gtk-6.0"
    ps = "  9 pmbootstrap build --arch aarch64 --src /work/x webkit2gtk-6.0\n"
    assert buildroot.foreign_build(ps) == "webkit2gtk-6.0"
