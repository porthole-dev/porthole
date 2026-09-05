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


def test_pkill_dash_f_is_an_error():
    text = "#!/bin/bash\npkill -f epiphany\n"
    found = tc.check_pkill_pattern("ph-thing.sh", text)
    assert len(found) == 1 and found[0].severity == "error"


def test_pkill_without_dash_f_is_left_alone():
    """`pkill epiphany` matches a process NAME, not a command line, so it
    cannot match the ssh invocation carrying it. Only -f has that defect."""
    assert tc.check_pkill_pattern("ph-thing.sh", "#!/bin/bash\npkill epiphany\n") == []


def test_an_explicit_exemption_is_honoured():
    """Some caller will have a real reason. The exemption is a marker in the
    source, so the reason is reviewable and greppable -- not an allowlist in
    another file that nobody reads next to the code."""
    text = "#!/bin/bash\npkill -f thing  # contract: pkill-ok pid is unknowable here\n"
    assert tc.check_pkill_pattern("ph-thing.sh", text) == []


def test_the_pkill_exemption_must_carry_a_reason():
    text = "#!/bin/bash\npkill -f thing  # contract: pkill-ok\n"
    found = tc.check_pkill_pattern("ph-thing.sh", text)
    assert len(found) == 1 and "reason" in found[0].detail


def test_a_fixed_timeout_on_ssh_is_a_warning():
    text = '#!/bin/bash\ntimeout 12 ssh "$PHONE" true\n'
    found = tc.check_fixed_timeout("ph-thing.sh", text)
    assert len(found) == 1 and found[0].severity == "warn"


def test_a_fixed_timeout_on_scp_counts_too():
    text = '#!/bin/bash\ntimeout 30 scp "$PHONE:/tmp/x" .\n'
    assert len(tc.check_fixed_timeout("ph-thing.sh", text)) == 1


def test_a_variable_ceiling_is_not_flagged():
    """The point is not that timeouts are bad -- it is that a LITERAL is a
    guess nobody can override. A variable already has the escape hatch."""
    text = '#!/bin/bash\ntimeout "${PH_RUN_TIMEOUT:-30}" ssh "$PHONE" true\n'
    assert tc.check_fixed_timeout("ph-thing.sh", text) == []


def test_a_timeout_on_something_that_is_not_the_device_is_ignored():
    assert tc.check_fixed_timeout("ph-thing.sh", "#!/bin/bash\ntimeout 5 make\n") == []


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
