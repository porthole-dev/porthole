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


def test_the_full_flash_cannot_run_in_the_sandbox_and_names_the_loop_device():
    op = plan.op("flash-full")
    assert op.sites[plan.SANDBOX] is not True
    assert "loop" in op.sites[plan.SANDBOX]


def test_an_irreversible_op_is_marked_so():
    """Confirm tiers derive from this, so a wrong value here is a device
    written without a gate."""
    assert plan.op("flash-full").reversible is False
    assert plan.op("install").reversible is False
    assert plan.op("mod").reversible is True


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
    op = plan.op("flash-boot")
    problems = plan.unmet(op, {"state": "BOOTED", "tree": True,
                               "kernel_pkg": "k", "defconfig": "d",
                               "arch": "aarch64", "dtb": "x",
                               "workdir": "/tmp", "free_gb": 100.0})
    assert any("FASTBOOT" in p for p in problems)


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
