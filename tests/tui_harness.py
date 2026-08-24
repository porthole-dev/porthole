#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Skip guard and runner for the console tests.

CI runs every tests/test_*.py on 3.8, 3.11 and 3.13 with textual absent, and
the console needs 3.10 plus textual. So these files must exit 0 there -- but
LOUDLY. This repo already learned that a linter printing "skipping" and exiting
0 reads exactly like a clean run: 28 findings reached CI that way. The `console`
CI job therefore asserts that these tests did NOT skip, which is what makes the
skip honest rather than a hole.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

MIN_PY = (3, 10)


def _available():
    if sys.version_info < MIN_PY:
        return False, "needs python {}.{}, found {}".format(
            MIN_PY[0], MIN_PY[1], ".".join(str(n) for n in sys.version_info[:3]))
    try:
        import textual  # noqa: F401
    except ImportError:
        return False, "textual is not installed"
    return True, ""


AVAILABLE, _REASON = _available()


def require():
    """Exit 0 with a loud SKIP if the console cannot run in this interpreter."""
    if not AVAILABLE:
        print("SKIP  {} ({})".format(pathlib.Path(sys.argv[0]).name, _REASON))
        sys.exit(0)


def run(namespace):
    """The repo's standard runner. Pass `globals()`."""
    tests = [(n, f) for n, f in sorted(namespace.items())
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


def pilot(app_factory, body):
    """Drive an app with Textual's pilot. `body` is an async fn taking (app, pilot)."""
    async def _go():
        app = app_factory()
        async with app.run_test() as p:
            await body(app, p)
    asyncio.run(_go())
