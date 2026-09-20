#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Which kernel is the phone running, and where did it come from?

An agent built one module from a tree for an experiment and later moved to
venus work expecting the aport's 19 venus patches to be present. They were
not, and NOTHING said so: no /dev/video7, no venus module, nothing in dmesg or
the journal -- an entirely silent absence.

The stamp that answers this has been on every device all along. The kernel
APKBUILD sets KBUILD_BUILD_VERSION="$((pkgrel + 1))-$_flavor", so `uname -v`
names the aport release; an envkernel build never runs that line and carries
the tree's own counter with no flavor suffix.

The fixtures are read from the live phone, 2026-08-31.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_provenance as prov                          # noqa: E402

# Real, from the phone. Truncated by the kernel at 64 bytes -- utsname.version
# is a 65-byte field -- which is why the timestamp ends mid-second. The prefix
# is what this reads and it survives.
APORT = "#22-postmarketos-qcom-msm8998-7.2 SMP PREEMPT Sat Aug 29 14:59:5"

# An envkernel build: pmbootstrap packages objects the tree already compiled,
# so the APKBUILD's make line never runs and KBUILD_BUILD_VERSION is unset.
TREE = "#7 SMP PREEMPT Fri Aug 29 11:02:14 UTC 2026"

# Verbatim `apk info -v` output captured from this phone. The PROBE's grep
# (^linux-(postmarketos|[a-z]+-)) lets ALL five of these through, not just the
# kernel -- linux-firmware-* and linux-pam-* match it too. A name match is the
# only thing that picks the right line.
APK_LINES = """\
linux-firmware-ath10k-20260622-r0
linux-firmware-qca-20260622-r0
linux-pam-1.7.1-r2
linux-pam-systemd-1.7.1-r2
linux-postmarketos-qcom-msm8998-7.2-7.2.2-r21"""

KPKG = "linux-postmarketos-qcom-msm8998-7.2"


class _FakeDevice:
    """No ssh, no phone -- just returns the PROBE's canned shape."""

    def __init__(self, proc_version, apk_lines):
        self._out = "\n".join([proc_version, "<<>>", apk_lines, "<<>>", ""])

    def run(self, command, timeout=None):
        return self._out


def test_an_aport_build_names_its_pkgrel():
    got = prov.parse_build_version(APORT)
    assert got["kind"] == "aport", got
    # pkgrel + 1 is what the APKBUILD stamps, so #22 means r21.
    assert got["pkgrel"] == 21, got
    assert got["flavor"] == "postmarketos-qcom-msm8998-7.2", got


def test_a_tree_build_has_no_flavor_and_is_not_an_aport():
    got = prov.parse_build_version(TREE)
    assert got["kind"] == "tree", got
    assert got["pkgrel"] is None, got


def test_unparseable_is_unknown_never_guessed():
    assert prov.parse_build_version("")["kind"] == "unknown"
    assert prov.parse_build_version("Linux version 7.2.2")["kind"] == "unknown"


def test_matching_pkgrel_is_done():
    running = {"build_version": APORT, "apk_pkgrel": "21"}
    state, evidence = prov.compare(running, "7.2.2", "21")
    assert state == "done", (state, evidence)
    assert "r21" in evidence, evidence


def test_a_device_behind_the_checkout_is_todo_and_names_both():
    running = {"build_version": APORT, "apk_pkgrel": "21"}
    state, evidence = prov.compare(running, "7.2.2", "22")
    assert state == "todo", (state, evidence)
    assert "r21" in evidence and "r22" in evidence, evidence


def test_boot_and_rootfs_desync_is_reported():
    """`fast` flashes boot only and leaves the rootfs apk db untouched.

    So uname says one pkgrel and the device's own apk database says another.
    That is the LADDER's documented trigger for the `kernel` rung and nothing
    detected it before.
    """
    running = {"build_version": APORT, "apk_pkgrel": "19"}
    state, evidence = prov.compare(running, "7.2.2", "21")
    assert state == "todo", (state, evidence)
    assert "desync" in evidence.lower(), evidence


def test_a_tree_build_is_blocked_and_says_the_series_may_be_absent():
    running = {"build_version": TREE, "apk_pkgrel": "21"}
    state, evidence = prov.compare(running, "7.2.2", "22")
    assert state == "blocked", (state, evidence)
    assert "tree" in evidence.lower(), evidence
    assert "patch" in evidence.lower(), evidence


def test_running_matches_by_name_not_first_line():
    """Five packages survive the PROBE's grep; only one is the kernel.

    Without a name match, the first line wins -- linux-firmware-ath10k-...-r0
    -- and the kernel's actual r21 is never seen.
    """
    proc_version = ("Linux version 7.2.2 (build@host) " + APORT)
    dev = _FakeDevice(proc_version, APK_LINES)
    info = prov.running(dev, KPKG)
    assert info["apk_pkgrel"] == "21", info
    state, evidence = prov.compare(info, "7.2.2", "21")
    assert state == "done", (state, evidence)


def test_running_without_kpkg_never_invents_a_desync():
    """The finding: kpkg='' used to fall back to first-line-wins (r0), which
    reported a confident, false boot/rootfs DESYNC against a device that is
    perfectly in sync. Now an unmatched apk_pkgrel stays empty, and compare()
    falls through to the plain pkgrel comparison instead of guessing.
    """
    proc_version = ("Linux version 7.2.2 (build@host) " + APORT)
    dev = _FakeDevice(proc_version, APK_LINES)
    info = prov.running(dev, "")
    assert info["apk_pkgrel"] == "", info
    state, evidence = prov.compare(info, "7.2.2", "21")
    assert state == "done", (state, evidence)
    assert "desync" not in evidence.lower(), evidence


# -- content, not just the label -------------------------------------------
#
# 2026-09-20: brief said "done -- aport r79, matching the checkout" while the
# phone's DTB and the tree's differed by 8 bytes (one power-domains entry, the
# fix for a camera broken all session). The label matched. The content did
# not, and no pkgrel can see that.

def test_matching_content_is_done():
    import porthole_provenance as prov

    same = {"sha": "a" * 64, "size": 97251}
    state, why = prov.compare_dtb(same, dict(same))
    assert state == "done"
    assert "97251" in why


def test_drifted_content_is_caught_and_names_both_sides():
    import porthole_provenance as prov

    state, why = prov.compare_dtb({"sha": "89236401" + "0" * 56, "size": 97251},
                                  {"sha": "a4343929" + "0" * 56, "size": 97243})
    assert state == "todo"
    assert "89236401" in why and "a4343929" in why
    assert "97251" in why and "97243" in why


def test_an_unreadable_device_blocks_rather_than_passes():
    """Empty must mean unknown, never 'unchanged'."""
    import porthole_provenance as prov

    state, _ = prov.compare_dtb({}, {"sha": "b" * 64, "size": 1})
    assert state == "blocked"


def test_no_built_dtb_skips_instead_of_claiming_a_match():
    import porthole_provenance as prov

    state, why = prov.compare_dtb({"sha": "b" * 64, "size": 1}, {})
    assert state == "skip"
    assert "says nothing" in why


def test_the_slot_comes_from_the_running_kernel():
    """The profile records intent; the A/B retry counter overrides it."""
    import porthole_provenance as prov

    assert prov.slot_suffix("x androidboot.slot_suffix=_a y") == "a"
    assert prov.slot_suffix("no slot here") == ""


def test_the_tree_dtb_path_mirrors_ph_build():
    import porthole_provenance as prov

    got = prov.tree_dtb_path({"PORTHOLE_KERNEL_TREE": "/t",
                              "PORTHOLE_ARCH_DIR": "arm64",
                              "PORTHOLE_DTB": "qcom/msm8998-google-taimen",
                              "PORTHOLE_DTB_FILE": "msm8998-google-taimen.dtb"})
    assert got == "/t/.output/arch/arm64/boot/dts/qcom/msm8998-google-taimen.dtb"


def test_an_incomplete_profile_yields_no_path():
    import porthole_provenance as prov

    assert prov.tree_dtb_path({}) == ""


def test_a_missing_dtb_file_is_absent_not_zero_length():
    import porthole_provenance as prov

    assert prov.dtb_in_tree("/nonexistent/none.dtb") == {}
    assert prov.dtb_in_tree("") == {}



# -- the userspace half ----------------------------------------------------

class _FakeDev:
    def __init__(self, text):
        self.text = text

    def run(self, command, timeout=12):
        return self.text


def test_a_stale_device_package_is_reported_with_both_versions():
    import porthole_provenance as prov

    state, why = prov.compare_packages({"device-google-taimen": "1-r60"},
                                       {"device-google-taimen": "1-r62"})
    assert state == "todo"
    assert "1-r60" in why and "1-r62" in why


def test_matching_packages_pass():
    import porthole_provenance as prov

    state, _ = prov.compare_packages({"a": "1-r1"}, {"a": "1-r1"})
    assert state == "done"


def test_a_package_only_in_the_checkout_is_not_called_drift():
    """Absent is a missing install, not a stale one. Different fix."""
    import porthole_provenance as prov

    state, _ = prov.compare_packages({"a": "1-r1"}, {"a": "1-r1", "b": "2-r2"})
    assert state == "done"


def test_a_subpackage_is_not_mistaken_for_its_parent():
    """device-google-taimen-fingerprint must not supply the parent's release."""
    import porthole_provenance as prov

    dev = _FakeDev("device-google-taimen-fingerprint-1-r60\n"
                   "device-google-taimen-1-r62\n")
    got = prov.installed_versions(dev, ["device-google-taimen",
                                        "device-google-taimen-fingerprint"])
    assert got["device-google-taimen"] == "1-r62"
    assert got["device-google-taimen-fingerprint"] == "1-r60"


def test_a_mute_device_yields_nothing_rather_than_a_pass():
    import porthole_provenance as prov

    assert prov.installed_versions(_FakeDev(""), ["a"]) == {}
    assert prov.compare_packages({}, {"a": "1-r1"})[0] == "skip"



# -- work that is in the tree and in no package ----------------------------

def test_a_dirty_kernel_tree_is_reported_with_the_count():
    import porthole_provenance as prov

    state, why = prov.compare_tree({"path": "/t", "branch": "v7.2", "dirty": 2})
    assert state == "todo"
    assert "2 file(s)" in why and "/t" in why
    assert "evaporate" in why


def test_a_clean_tree_passes():
    import porthole_provenance as prov

    assert prov.compare_tree({"path": "/t", "branch": "m", "dirty": 0})[0] == "done"


def test_no_tree_skips_rather_than_passing():
    import porthole_provenance as prov

    assert prov.compare_tree({})[0] == "skip"


def test_a_path_that_is_not_a_checkout_yields_nothing():
    import porthole_provenance as prov

    assert prov.dirty_tree("/nonexistent/tree") == {}
    assert prov.dirty_tree("") == {}



if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
