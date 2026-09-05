#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The contract checks, tested against fixtures rather than the live tree.

WHY FIXTURES
    A check asserted against tools/ is a test that changes meaning every time
    somebody edits a tool. These tests must answer one question only -- does
    the checker recognise the thing it claims to recognise -- so each one
    hands it a string. The live tree is what `porthole tools audit` reports
    on, and that is a report, not an assertion.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_toolcontract as tc                          # noqa: E402


def test_shared_exit_codes_pass():
    text = "#!/bin/bash\n# exits: 0 ok · 1 failed · 64 usage\n"
    assert tc.check_exit_codes("ph-thing.sh", text) == []


def test_an_unshared_exit_code_is_an_error():
    text = "#!/bin/bash\n# exits: 0 ok · 3 something\n"
    found = tc.check_exit_codes("ph-thing.sh", text)
    assert len(found) == 1
    assert found[0].severity == "error"
    assert "3" in found[0].detail


def test_see_source_is_not_an_api():
    text = "#!/bin/bash\n# exits: 0 ok · 3 see source\n"
    found = tc.check_exit_codes("ph-thing.sh", text)
    assert any("see source" in f.detail for f in found), found


def test_two_is_flagged_because_usage_is_64():
    """Ten tools use 2 for usage and nine use 64. One of them is wrong, and
    the shared constant is what decides which."""
    text = "#!/bin/bash\n# exits: 0 ok · 2 usage\n"
    found = tc.check_exit_codes("ph-thing.sh", text)
    assert len(found) == 1 and "2" in found[0].detail


def test_a_tool_with_no_exits_field_is_not_this_checks_problem():
    """A missing header field is already caught by test_tools.py; reporting it
    twice makes the audit noisy and the two checks drift."""
    assert tc.check_exit_codes("ph-thing.sh", "#!/bin/bash\n") == []


def test_shared_exits_match_the_cli_constants():
    """SHARED_EXITS is written as literals so a checker cannot be broken by an
    import cycle. That is only safe while something asserts the two agree."""
    import porthole_cli as cli
    expected = {cli.EX_OK, cli.EX_FAIL, cli.EX_USAGE, cli.EX_UNAVAILABLE,
                cli.EX_LOCK, cli.EX_STATE, cli.EX_TIMEOUT, cli.EX_INTERRUPT}
    assert set(tc.SHARED_EXITS) == expected


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
