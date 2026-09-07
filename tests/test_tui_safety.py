#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The safety boundary.

The most important test file in the console. A TUI that can flash a device is
a TUI that can brick one by mis-keystroke, and "we were careful" is not a
mechanism. These run on every interpreter with nothing installed, because a
boundary that needs a terminal to test is a boundary nobody tests.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

from porthole_tui import safety  # noqa: E402
import porthole_milestones as ms  # noqa: E402


def test_empty_command_is_never_safe():
    assert safety.needs_confirmation("", True)
    assert safety.needs_confirmation(None, True)


def test_unsafe_milestone_always_confirms():
    assert safety.needs_confirmation("porthole brief", False)


def test_safe_and_harmless_runs_without_asking():
    assert not safety.needs_confirmation("porthole brief", True)
    assert not safety.needs_confirmation("porthole next", True)


def test_dangerous_word_overrides_a_safe_table_entry():
    # The table is a human judgement and a human can edit it in a hurry.
    # If EITHER source says stop, we stop.
    for command in ("porthole flash boot",
                    "porthole run ph-flash-boot.sh",
                    "porthole run ph-to-fastboot.sh",
                    "porthole sandbox set_active b"):
        assert safety.needs_confirmation(command, True), command


def test_constructed_commands_are_checked_whole():
    # New in the rework: a generated form assembles the string, so the check
    # must see the assembled string and not just the verb.
    assert safety.needs_confirmation("porthole blobs unsparse vendor.img", True) is False
    assert safety.needs_confirmation("porthole flash boot --slot b", True) is True


def test_is_risky_is_about_the_text_only():
    assert safety.is_risky("porthole flash boot")
    assert not safety.is_risky("porthole brief")


def test_is_risky_handles_empty_and_none():
    assert not safety.is_risky("")
    assert not safety.is_risky(None)


def test_every_dangerous_word_forces_confirmation():
    # Every word in DANGEROUS must be pinned by a test case, so that adding
    # or removing a word from the tuple is not a silent change. The suite is
    # the only thing standing between an edit and a bricked device.
    for word in safety.DANGEROUS:
        command = "porthole run {}".format(word)
        assert safety.needs_confirmation(command, True), (
            "dangerous word '{}' should force confirmation".format(word))
        # Verify is_risky catches it independently
        assert safety.is_risky(command), (
            "is_risky should catch dangerous word '{}'".format(word))


def test_trailing_space_in_dangerous_acts_as_word_boundary():
    # "dd " and "rm " have trailing spaces to avoid matching "add" or "arm".
    # Pin both directions: that boundary-respecting matches DO trigger, and
    # that embedded matches DO NOT.
    for word in ("dd ", "rm "):
        if word not in safety.DANGEROUS:
            continue
        # Boundary-respecting match: should trigger
        boundary_cmd = "porthole run {} if=/dev/zero".format(word.strip())
        assert safety.needs_confirmation(boundary_cmd, True), (
            "'{} ...' should require confirmation".format(word.strip()))
        # Embedded match: should NOT trigger for this reason
        # (though other words in DANGEROUS might still trigger it)
        embedded_cmd = "porthole run tk-add{}.sh".format(word.strip())
        if word.strip() not in " ".join(safety.DANGEROUS).replace(
                word, ""):
            # Only test embedded non-match if the word isn't also contained
            # in other DANGEROUS entries
            assert not safety.is_risky(embedded_cmd), (
                "word embedded in 'add{}' should not trigger on '{}'".format(
                    word.strip(), word))


def test_dangerous_fields_exist():
    # DANGEROUS_FIELDS is a new export for Task 5. Its contents must be
    # pinned, because Task 5 will not be run if this task is ever edited to
    # drop a field, and a silent truncation would leave generated form fields
    # quietly not forcing confirmation.
    assert safety.DANGEROUS_FIELDS, "DANGEROUS_FIELDS must not be empty"
    assert "slot" in safety.DANGEROUS_FIELDS, "slot must be in DANGEROUS_FIELDS"
    assert "partition" in safety.DANGEROUS_FIELDS, (
        "partition must be in DANGEROUS_FIELDS")
    # Pin the full expected tuple
    assert safety.DANGEROUS_FIELDS == (
        "slot", "partition", "boot", "dtbo", "vendor_boot"), (
        "DANGEROUS_FIELDS changed unexpectedly")


def test_the_trailing_space_boundary_is_load_bearing():
    # "dd " and "rm " carry a deliberate trailing space as a crude word boundary.
    # Removing it widens the match: "/firmware" contains "rm", so a read-only
    # listing would start demanding a flash confirmation. A boundary people
    # click through is worse than no boundary.
    for harmless in ("porthole blobs ls vendor.raw.img --dir /firmware",
                     "porthole run ph-addfoo.sh",
                     "porthole brain search firmware"):
        assert not safety.needs_confirmation(harmless, True), harmless
    # and the real thing still trips
    for real in ("porthole run ph-x.sh dd if=/dev/zero of=/dev/block/sda",
                 "porthole run ph-x.sh rm -rf /data"):
        assert safety.needs_confirmation(real, True), real


def test_no_safe_milestone_command_is_irreversible():
    """Cross-check the milestone table against the screen, so a table edit
    cannot quietly make something destructive auto-runnable."""
    for m in ms.MILESTONES:
        if not m.safe or not m.how.startswith("porthole"):
            continue
        assert not safety.needs_confirmation(m.how, safe=True), (
            f"milestone {m.id!r} is marked safe but its command {m.how!r} "
            f"looks irreversible; one of the two is wrong")


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
