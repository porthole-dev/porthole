# SPDX-License-Identifier: MIT
"""Can this interpreter run the console, and if not, what exactly do I type?

Imported on EVERY interpreter the repo supports, including the 3.8 floor, so
it must not import textual, the app, or anything under lib/porthole_tui that
does. It is the one module in this package with that property, and the gate
test asserts it.

Separate from __main__ because a message is a decision, and a decision that
cannot be tested without a terminal is a decision nobody tests.
"""
from __future__ import annotations

MIN_PY = (3, 10)

_OLD = (
    "porthole's console needs python {want} or newer; this is {have}.\n"
    "\n"
    "  The rest of porthole does not. `porthole next`, `porthole doctor`,\n"
    "  `porthole brief` and every tool still run on python 3.8 with nothing\n"
    "  installed -- only the console needs a newer interpreter.\n")

_NO_TEXTUAL = (
    "porthole's console needs the `textual` package, which is not installed.\n"
    "\n"
    "  {hint}\n"
    "\n"
    "  It is an optional extra. The CLI has no third-party dependencies and\n"
    "  never will; if you would rather not install anything, `porthole next`\n"
    "  answers the same question in one line.\n")


def preflight(version, textual, family):
    """Return "" if the console can run, else the whole message to print.

    Pure on purpose: every branch is reachable from a test on any interpreter,
    including the ones that could never run the console themselves.
    """
    if tuple(version[:2]) < MIN_PY:
        return _OLD.format(
            want="{}.{}".format(MIN_PY[0], MIN_PY[1]),
            have=".".join(str(n) for n in version[:3]))
    if not textual:
        try:
            from porthole_cmd_doctor import install_hint
            hint = install_hint("textual", family)
        except Exception:  # noqa: BLE001
            hint = "pip install --user 'textual>=8,<9'"
        return _NO_TEXTUAL.format(hint=hint)
    return ""


def check():
    """preflight() against the live interpreter."""
    import sys
    try:
        import textual  # noqa: F401
        have = True
    except ImportError:
        have = False
    family = "unknown"
    try:
        from porthole_cmd_doctor import distro_family
        family = distro_family()
    except Exception:  # noqa: BLE001
        pass
    return preflight(sys.version_info, have, family)
