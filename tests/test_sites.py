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


def test_a_full_flash_can_run_in_the_sandbox_now_the_assembler_covers_it():
    """Before `_ph_assemble_image` (tools/ph-build.sh) existed, minting a
    rootfs image needed a loop device the sandbox's user namespace can never
    own, so this refused the sandbox with a NO_LOOP reason. Gate C5 proved
    that stale: .run/build-tkflash-20260908-154750.log opens ">> NOTE: this
    image was built from /work/linux-ws" (the container mount), and the full
    flash it fed succeeded. See test_plan.py for the manifest-level pin."""
    op = plan.op("flash-full")
    site, why = sites.choose_from(op, available=[plan.SANDBOX])
    assert site == plan.SANDBOX, why


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
    package repo. No real op currently refuses either site -- every Op now
    defaults to {SANDBOX: True, HOST: True}, since the assembler made both
    able to mint a rootfs image -- so a synthetic Op exercises the
    mechanism itself rather than a manifest value that no longer refuses
    anything."""
    op = plan.Op("fake-op", "exercises choose_from only",
                 sites={plan.SANDBOX: "made up for this test", plan.HOST: True})
    site, why = sites.choose_from(op, available=[plan.SANDBOX, plan.HOST],
                                  prefer=plan.SANDBOX)
    assert site is None
    assert why == "made up for this test"


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


def test_facts_free_gb_walks_up_to_an_existing_ancestor():
    """A fresh host has no pmbootstrap work dir yet -- and is precisely the
    host about to need ~29 GB it does not know it lacks. Without walking up
    to a real directory, os.statvfs on a path that does not exist raises,
    free_gb comes back None, and unmet() skips the space check entirely on
    the one host where running out is most likely."""
    import tempfile

    real_usable = sites.usable
    sites.usable = _no_real_workspace
    try:
        existing = pathlib.Path(tempfile.mkdtemp(prefix="porthole-facts-"))
        nowhere = existing / "not" / "yet" / "created"
        cfg = {"PORTHOLE_WORKDIR": str(existing),
               "PORTHOLE_PMB_DIR": str(nowhere)}
        got = sites.facts(_FakeCtx(cfg))
        assert isinstance(got["free_gb"], float), got
    finally:
        sites.usable = real_usable


def _chroot(root, populated: bool):
    info = root / "chroot_rootfs_google-taimen"
    if populated:
        info = info / "usr" / "share" / "deviceinfo"
        info.mkdir(parents=True)
        (info / "deviceinfo").write_text("deviceinfo_format_version=0\n")
    else:
        info.mkdir(parents=True)


def test_facts_reads_the_chroot_of_the_resolved_site_not_a_guess_false_refusal():
    """Reproduced: an installed HOST chroot, `--host` (site=HOST), and an
    UNRELATED sandbox workspace happens to be running. `facts()` used to
    call `usable(ctx)[0]` on its own and read the SANDBOX's chroot instead
    -- wrongly reporting "no rootfs chroot has been installed" for a host
    that has one."""
    import tempfile

    import porthole_cmd_sandbox as sandbox

    real_usable = sites.usable
    real_sandbox_pmb = sandbox._sandbox_pmb
    host_dir = pathlib.Path(tempfile.mkdtemp(prefix="porthole-hostchroot-"))
    sandbox_dir = pathlib.Path(tempfile.mkdtemp(prefix="porthole-sandboxchroot-"))
    _chroot(host_dir, populated=True)      # the HOST is properly installed
    _chroot(sandbox_dir, populated=False)  # the unrelated sandbox is not
    sites.usable = lambda ctx: (True, "")  # the unrelated workspace IS up
    sandbox._sandbox_pmb = lambda cfg: sandbox_dir
    try:
        cfg = {"PORTHOLE_WORKDIR": str(host_dir),
               "PORTHOLE_PMB_DIR": str(host_dir),
               "PORTHOLE_DEVICE": "google-taimen"}
        got = sites.facts(_FakeCtx(cfg), site=plan.HOST)
        assert got[plan.CHROOT_INSTALLED] is True, got
    finally:
        sites.usable = real_usable
        sandbox._sandbox_pmb = real_sandbox_pmb


def test_facts_reads_the_chroot_of_the_resolved_site_not_a_guess_false_clear():
    """Reproduced, and the dangerous direction: an UNINSTALLED HOST chroot,
    `--host` (site=HOST), and a POPULATED sandbox chroot. `facts()` used to
    read the sandbox's (installed) chroot and report CHROOT_INSTALLED=True
    -- no refusal at all for a host that cannot actually export. This is
    the exact failure class the rework exists to end: a preflight that
    says yes because it looked at the wrong machine."""
    import tempfile

    import porthole_cmd_sandbox as sandbox

    real_usable = sites.usable
    real_sandbox_pmb = sandbox._sandbox_pmb
    host_dir = pathlib.Path(tempfile.mkdtemp(prefix="porthole-hostchroot-"))
    sandbox_dir = pathlib.Path(tempfile.mkdtemp(prefix="porthole-sandboxchroot-"))
    _chroot(host_dir, populated=False)     # the HOST is NOT installed
    _chroot(sandbox_dir, populated=True)   # the unrelated sandbox is
    sites.usable = lambda ctx: (True, "")  # the unrelated workspace IS up
    sandbox._sandbox_pmb = lambda cfg: sandbox_dir
    try:
        cfg = {"PORTHOLE_WORKDIR": str(host_dir),
               "PORTHOLE_PMB_DIR": str(host_dir),
               "PORTHOLE_DEVICE": "google-taimen"}
        got = sites.facts(_FakeCtx(cfg), site=plan.HOST)
        assert got[plan.CHROOT_INSTALLED] is False, got
    finally:
        sites.usable = real_usable
        sandbox._sandbox_pmb = real_sandbox_pmb


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
