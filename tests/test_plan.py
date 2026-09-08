#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The operation manifest, and the drift gate on it.

WHY THIS EXISTS
    What an operation needs was written in nine places that could not see one
    another -- _preflight, export_problems, _workspace_usable,
    _ph_can_make_image, ph_need_fastboot, LADDER, TREE_RUNGS, EXPORT_RUNGS,
    INSTALL_RUNGS -- and `porthole flash --yes` spent twelve minutes before
    discovering that the site it had chosen could not produce the artefact it
    needed. One manifest, and a test that fails when a verb grows a
    prerequisite the manifest does not declare.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

import porthole_plan as plan  # noqa: E402


def test_every_op_declares_a_device_state():
    """`needs: BOOTED` has been a tool contract since the toolbox existed and
    was never available for a verb, which is why "build fast needs the phone
    up" was knowledge you could only get by running it."""
    for name, op in plan.OPS.items():
        assert op.needs_state in (plan.NONE, plan.ANY, plan.BOOTED,
                                  plan.FASTBOOT), name


def test_a_site_that_cannot_run_an_op_says_why():
    """The whole failure being fixed: the workspace could not make a rootfs
    image, and the refusal said so only from inside a shell function."""
    for name, op in plan.OPS.items():
        for site, reason in op.sites.items():
            assert site in (plan.SANDBOX, plan.HOST), (name, site)
            if reason is not True:
                assert isinstance(reason, str) and len(reason) > 20, (
                    f"{name}/{site} is excluded with no usable reason")


def test_the_full_flash_and_install_can_both_run_in_the_sandbox():
    """Stale until this fix: `sites={SANDBOX: NO_LOOP, HOST: True}` was true
    before `_ph_assemble_image` (tools/ph-build.sh) existed to build the
    rootfs image straight from the chroot with no loop device. Gate C5
    proved it false -- .run/build-tkflash-20260908-154750.log opens ">> NOTE:
    this image was built from /work/linux-ws" (the container mount), and the
    full flash it fed succeeded."""
    for name in ("flash-full", "install"):
        assert plan.op(name).sites[plan.SANDBOX] is True, name


def test_an_irreversible_op_is_marked_so():
    """Confirm tiers derive from this, so a wrong value here is a device
    written without a gate. `flash-full` is the only op that earns this
    tier: it is the irreversible act, not the host-side rebuild `image` and
    `install` do on the way there -- rebuilding regenerates the artefact it
    overwrites, so both stay reversible and share the same `destroys`."""
    assert plan.op("flash-full").reversible is False
    assert plan.op("mod").reversible is True
    assert plan.op("install").reversible is True
    assert plan.op("image").reversible is True
    assert plan.op("image").destroys == plan.op("install").destroys


def test_ops_that_reach_the_device_over_ssh_declare_booted():
    """A mutation from BOOTED to FASTBOOT on `boot` passed the whole suite
    once -- nothing pinned it. `mod`, `boot`, `fast` and `upgrade` all ssh
    into the device before moving it (tkboot seeds its base image with
    `tk_run 'uname -r'` before it ever calls `ph-to-fastboot.sh`)."""
    for name in ("mod", "boot", "fast", "upgrade"):
        assert plan.op(name).needs_state == plan.BOOTED, name


def test_flash_full_needs_the_bootloader_but_flash_boot_accepts_either():
    """External fact, not a manifest round-trip: `tkflash` (flash-full) runs
    `pmbootstrap flasher flash_rootfs`, which has no booted-device fallback
    and genuinely needs the device already in the bootloader. `tkflash-boot`
    (flash-boot) does not -- tools/ph-build.sh has it check
    `tk_in_fastboot || "$_PH_REPO/tools/ph-to-fastboot.sh" || return 1`,
    which accepts FASTBOOT as-is and moves a BOOTED device there itself.
    Declaring FASTBOOT for flash-boot refused a correctly booted phone for
    an operation that would have worked -- reproduced on hardware
    2026-09-08."""
    assert plan.op("flash-full").needs_state == plan.FASTBOOT
    assert plan.op("flash-boot").needs_state == plan.ANY


def test_fast_requires_an_installed_chroot():
    """Fixing the facts dicts above to include chroot_installed left nothing
    asserting `fast` actually needs it -- dropping CHROOT_INSTALLED from its
    `needs` left the suite green."""
    op = plan.op("fast")
    problems = plan.unmet(op, {"state": "BOOTED", "tree": True,
                               "kernel_pkg": "linux-x", "defconfig": "d",
                               "arch": "aarch64", "dtb": "x",
                               "workdir": "/tmp", "free_gb": 100.0,
                               "chroot_installed": False})
    assert plan._MISSING[plan.CHROOT_INSTALLED] in problems


def test_unmet_is_pure_and_names_the_fix():
    op = plan.op("fast")
    problems = plan.unmet(op, {"state": "BOOTED", "tree": False,
                               "kernel_pkg": "linux-x", "defconfig": "d",
                               "arch": "aarch64", "dtb": "x",
                               "workdir": "/tmp", "free_gb": 100.0,
                               "chroot_installed": True})
    assert any("kernel tree" in p for p in problems)
    assert plan.unmet(op, {"state": "BOOTED", "tree": True,
                           "kernel_pkg": "linux-x", "defconfig": "d",
                           "arch": "aarch64", "dtb": "x",
                           "workdir": "/tmp", "free_gb": 100.0,
                           "chroot_installed": True}) == []


def test_a_state_mismatch_is_reported_as_state_not_as_failure():
    """flash-full is the op that actually gates on FASTBOOT -- see
    test_flash_full_needs_the_bootloader_but_flash_boot_accepts_either."""
    op = plan.op("flash-full")
    problems = plan.unmet(op, {"state": "BOOTED", "tree": True,
                               "kernel_pkg": "k", "defconfig": "d",
                               "arch": "aarch64", "dtb": "x",
                               "workdir": "/tmp", "free_gb": 100.0})
    assert any("FASTBOOT" in p for p in problems)


def test_flash_boot_state_is_never_reported_as_unmet():
    """tkflash-boot accepts either state and moves the device itself when it
    is not already in the bootloader (tools/ph-build.sh: `tk_in_fastboot ||
    "$_PH_REPO/tools/ph-to-fastboot.sh" || return 1`) -- no state a real
    device probe can return should show up in `unmet`'s problems."""
    op = plan.op("flash-boot")
    for state in ("BOOTED", "FASTBOOT", "ABSENT", "INITRAMFS", "FROZEN", ""):
        problems = plan.unmet(op, {"state": state, "dtb": "x"})
        assert problems == [], (state, problems)


# --------------------------------------------------------- the drift gate --
#
# §5/§12 of the design spec, and this module's own docstring, both promise a
# test that fails when a verb grows a prerequisite the manifest does not
# declare -- porthole_cmd_build.EXPORT_RUNGS and INSTALL_RUNGS are the
# tenth and eleventh place this knowledge lived (`_preflight`,
# `export_problems`, `_workspace_usable`, `_ph_can_make_image`,
# `ph_need_fastboot`, LADDER, TREE_RUNGS make nine), still consulted at
# porthole_cmd_build.py:974 and porthole_cmd_doctor.py:931, and until now
# nothing pinned them against the manifest they duplicate by hand.

def test_export_rungs_agrees_with_the_manifests_chroot_installed_need():
    """EXPORT_RUNGS ("fast", "upgrade") is hand-maintained prose for exactly
    one manifest fact: which BUILD_ACTIONS rungs need CHROOT_INSTALLED.
    `kernel` deliberately needs no chroot (it CREATES one) even though it
    also runs `pmbootstrap export` -- see porthole_cmd_build.py's comment on
    EXPORT_RUNGS. This fails the day the two disagree, rather than shipping
    a rung whose gating silently stopped matching what it claims to need."""
    import porthole_cmd_build as build
    derived = tuple(sorted(
        name for name in build.BUILD_ACTIONS
        if name != "auto" and plan.CHROOT_INSTALLED in plan.op(name).needs))
    assert tuple(sorted(build.EXPORT_RUNGS)) == derived


def test_install_rungs_agrees_with_the_manifests_rootfs_password_need():
    """INSTALL_RUNGS ("kernel", "image") is hand-maintained prose for which
    BUILD_ACTIONS rungs need ROOTFS_PW -- also read by
    porthole_cmd_doctor.py's PORTHOLE_PMOS_PASSWORD check."""
    import porthole_cmd_build as build
    derived = tuple(sorted(
        name for name in build.BUILD_ACTIONS
        if name != "auto" and plan.ROOTFS_PW in plan.op(name).needs))
    assert tuple(sorted(build.INSTALL_RUNGS)) == derived


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
