#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The preflight gate. Runs on EVERY interpreter, including 3.8 -- that is the
point of it, so it must not import textual or the app.

It deliberately does NOT use tui_harness: the harness would skip here, and the
gate is exactly the thing that has to work where the console cannot.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from porthole_tui import gate  # noqa: E402


def test_old_python_is_refused():
    msg = gate.preflight((3, 8, 10), textual=True, family="fedora")
    assert msg, "3.8 must be refused"
    assert "3.10" in msg, msg
    assert "3.8.10" in msg, "say what was actually found: " + msg


def test_missing_textual_names_the_install_command():
    msg = gate.preflight((3, 12, 0), textual=False, family="fedora")
    assert msg
    assert "textual" in msg
    assert "dnf" in msg or "pip" in msg, "must be copy-pasteable: " + msg


def test_unknown_distro_still_gets_a_command():
    msg = gate.preflight((3, 12, 0), textual=False, family="unknown")
    assert "pip install" in msg, msg


def test_a_good_environment_passes():
    assert gate.preflight((3, 12, 0), textual=True, family="fedora") == ""


def test_gate_does_not_drag_in_textual():
    assert "textual" not in sys.modules, \
        "importing porthole_tui.gate must not import textual"


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
