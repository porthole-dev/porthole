#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Where an operation can run, decided before anything is printed.

`porthole flash --yes` announced "building IN THE WORKSPACE", ran, and died
at the last step because the workspace cannot create a rootfs image. The
decision and the refusal both have to happen before the announcement.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

import porthole_plan as plan  # noqa: E402
import porthole_sites as sites  # noqa: E402


def test_the_sandbox_cannot_make_an_image_even_when_the_host_has_a_loop():
    """The host's /dev/loop-control is irrelevant inside a user namespace that
    does not own it. Answering from the host's node is the mistake."""
    assert sites.can_make_image(plan.HOST, loop_exists=True) is True
    assert sites.can_make_image(plan.SANDBOX, loop_exists=True) is False


def test_choosing_a_site_for_a_full_flash_refuses_the_sandbox_with_the_reason():
    op = plan.op("flash-full")
    site, why = sites.choose_from(op, available=[plan.SANDBOX])
    assert site is None
    assert "loop" in why


def test_a_rung_both_sites_can_run_prefers_the_sandbox():
    """The sandbox is the default because it needs no standing root. Silently
    falling back to the host is what made two divergent work dirs."""
    op = plan.op("mod")
    site, _ = sites.choose_from(op, available=[plan.SANDBOX, plan.HOST])
    assert site == plan.SANDBOX


def test_an_explicit_preference_is_honoured_when_the_site_can_run_it():
    op = plan.op("mod")
    site, _ = sites.choose_from(op, available=[plan.SANDBOX, plan.HOST],
                               prefer=plan.HOST)
    assert site == plan.HOST


def test_a_preference_for_a_site_that_cannot_run_it_is_refused_not_ignored():
    """Silently honouring the fallback is how a build ran against the wrong
    package repo."""
    op = plan.op("flash-full")
    site, why = sites.choose_from(op, available=[plan.SANDBOX, plan.HOST],
                                  prefer=plan.SANDBOX)
    assert site is None
    assert "loop" in why


# ------------------------------- the host half of "can this site make one" --

def test_a_host_with_no_loop_device_cannot_mint_a_rootfs_image_either():
    """The static `op.sites` table says HOST: True unconditionally for
    `install` -- correct about the SANDBOX (which can never do it) and
    unproven about the host, which is the other half of the plane's central
    claim: a host with no /dev/loop-control would be told it can build a
    rootfs image and then fail exactly the way the sandbox already refuses
    for."""
    op = plan.op("install")
    site, why = sites.choose_from(op, available=[plan.HOST],
                                  host_can_image=False)
    assert site is None
    assert "loop" in why


def test_a_host_with_a_loop_device_can_mint_a_rootfs_image():
    op = plan.op("install")
    site, why = sites.choose_from(op, available=[plan.HOST],
                                  host_can_image=True)
    assert site == plan.HOST, why


def test_host_can_image_is_irrelevant_to_an_op_that_produces_no_image():
    """`mod` never touches a rootfs image; a host with no loop device must
    not be refused for a reason that does not apply to it."""
    op = plan.op("mod")
    site, why = sites.choose_from(op, available=[plan.SANDBOX, plan.HOST],
                                  host_can_image=False)
    assert site == plan.SANDBOX, why


# -------------------------------------------------------------------- facts --

class _FakeCtx:
    def __init__(self, cfg):
        self.cfg = cfg


def _no_real_workspace(ctx):
    """`usable`, faked: a fake ctx must not depend on whatever podman
    container or device happens to be running on the machine the tests run
    on."""
    return False, "faked for the test"


def test_facts_reports_chroot_installed_true_only_when_populated():
    """The reviewer flipped this bit once already and nothing failed:
    `export_problems` returns [] when the chroot IS installed, so `not
    export_problems(...)` is True exactly when it is. `facts()` feeds every
    preflight decision an export rung makes, and an inversion here reads
    every one of them backwards."""
    import tempfile

    real_usable = sites.usable
    sites.usable = _no_real_workspace
    try:
        populated = pathlib.Path(tempfile.mkdtemp(prefix="porthole-facts-"))
        info = (populated / "chroot_rootfs_google-taimen" / "usr" / "share"
               / "deviceinfo")
        info.mkdir(parents=True)
        (info / "deviceinfo").write_text("deviceinfo_format_version=0\n")
        cfg = {"PORTHOLE_WORKDIR": str(populated),
               "PORTHOLE_PMB_DIR": str(populated),
               "PORTHOLE_DEVICE": "google-taimen"}
        got = sites.facts(_FakeCtx(cfg))
        assert got[plan.CHROOT_INSTALLED] is True, got

        empty = pathlib.Path(tempfile.mkdtemp(prefix="porthole-facts-"))
        (empty / "chroot_rootfs_google-taimen").mkdir(parents=True)
        cfg = {"PORTHOLE_WORKDIR": str(empty), "PORTHOLE_PMB_DIR": str(empty),
               "PORTHOLE_DEVICE": "google-taimen"}
        got = sites.facts(_FakeCtx(cfg))
        assert got[plan.CHROOT_INSTALLED] is False, got
    finally:
        sites.usable = real_usable


def test_facts_reads_the_profile_keys_it_promises_unmet():
    """The mapping `porthole_plan.unmet` consumes -- a key it reads that
    `facts()` does not set is a KeyError away from every preflight call, and
    was worth pinning down with no real host under it."""
    import tempfile

    real_usable = sites.usable
    sites.usable = _no_real_workspace
    try:
        workdir = pathlib.Path(tempfile.mkdtemp(prefix="porthole-facts-"))
        cfg = {"PORTHOLE_WORKDIR": str(workdir),
               "PORTHOLE_PMB_DIR": str(workdir),
               "PORTHOLE_KERNEL_PKG": "linux-x", "PORTHOLE_DEFCONFIG": "d",
               "PORTHOLE_ARCH": "aarch64", "PORTHOLE_DTB": "qcom/x"}
        got = sites.facts(_FakeCtx(cfg))
        assert got[plan.KERNEL_PKG] == "linux-x", got
        assert got[plan.DEFCONFIG] == "d", got
        assert got[plan.ARCH] == "aarch64", got
        assert got[plan.DTB] == "qcom/x", got
        assert got[plan.WORKDIR] is True, got
        assert got["state"] == "", got
    finally:
        sites.usable = real_usable


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
