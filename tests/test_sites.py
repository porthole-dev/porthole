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


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
