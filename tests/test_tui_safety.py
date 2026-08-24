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
sys.path.insert(0, str(ROOT / "lib"))

from porthole_tui import safety  # noqa: E402


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
                    "porthole run tk-flash-boot.sh",
                    "porthole run tk-to-fastboot.sh",
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


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL {}:\n  {}".format(name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("ERROR {}: {}: {}".format(name, type(exc).__name__, exc))
    print("{}/{} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
