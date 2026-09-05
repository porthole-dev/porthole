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


def test_a_comment_mentioning_pkill_is_not_a_finding():
    """A comment line warning never to use `pkill -f` documents the hazard;
    it is not the hazard. Two real tools carry exactly this comment and were
    both ranked as top-severity violators before the comment skip."""
    text = "#!/bin/bash\n# never use pkill -f over ssh\n"
    assert tc.check_pkill_pattern("ph-thing.sh", text) == []


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


def test_the_timeout_exemption_must_carry_a_reason():
    text = "#!/bin/bash\ntimeout 12 ssh \"$PHONE\" true  # contract: timeout-ok\n"
    found = tc.check_fixed_timeout("ph-thing.sh", text)
    assert len(found) == 1 and "reason" in found[0].detail


def test_a_timeout_exemption_with_a_reason_suppresses_the_finding():
    text = "#!/bin/bash\ntimeout 12 ssh \"$PHONE\" true  # contract: timeout-ok justified here\n"
    assert tc.check_fixed_timeout("ph-thing.sh", text) == []


def test_a_bare_sleep_is_a_warning():
    text = "#!/bin/bash\nreboot_it\nsleep 60\ncheck_it\n"
    found = tc.check_bare_sleep("ph-thing.sh", text)
    assert len(found) == 1 and found[0].severity == "warn"


def test_settling_hardware_is_a_legitimate_reason():
    """poll-never-sleep.md is about waiting for an EVENT you could poll for.
    A hardware settling delay is not that, and thirty tools contain one, so
    this must be a warning with an exemption rather than an error."""
    text = "#!/bin/bash\nsleep 2  # contract: sleep-ok i2c settling, nothing to poll\n"
    assert tc.check_bare_sleep("ph-thing.sh", text) == []


def test_a_short_sleep_inside_a_poll_loop_is_not_flagged():
    """`while ...; do ...; sleep 1; done` IS polling. Flagging it would push
    people toward busy loops, which is worse."""
    text = "#!/bin/bash\nwhile ! ready; do\n    sleep 1\ndone\n"
    assert tc.check_bare_sleep("ph-thing.sh", text) == []


def test_the_sleep_exemption_must_carry_a_reason():
    text = "#!/bin/bash\nsleep 30  # contract: sleep-ok\n"
    found = tc.check_bare_sleep("ph-thing.sh", text)
    assert len(found) == 1 and "reason" in found[0].detail


def test_a_sleep_in_a_long_poll_loop_is_not_flagged():
    """A realistic poll loop body spans more than six lines, so the window-based
    heuristic would incorrectly flag the sleep. do/done delimit the loop exactly."""
    text = """#!/bin/bash
while ! device_ready; do
    echo waiting
    check_usb
    check_serial
    check_uart
    check_battery
    check_temp
    sleep 1
done
"""
    assert tc.check_bare_sleep("ph-thing.sh", text) == []


def test_a_sleep_after_a_closed_loop_is_still_flagged():
    """When a loop closes with done, the depth counter must decrement. A sleep
    that appears after the loop ends is a bare sleep and must be flagged."""
    text = """#!/bin/bash
while ! ready; do
    sleep 1
done
sleep 30
"""
    found = tc.check_bare_sleep("ph-thing.sh", text)
    assert len(found) == 1 and found[0].severity == "warn"


def test_a_one_line_loop_does_not_leak_depth():
    """`for x in $Y; do ...; done` on one line nets to +1 then -1 under the
    do/done counter, so it must not silence a later top-level sleep. The old
    while/until/for opener counted this as an unmatched open."""
    text = "#!/bin/bash\nfor e in $EVENTS; do echo $e; done\nsleep 30\n"
    found = tc.check_bare_sleep("ph-thing.sh", text)
    assert len(found) == 1


def test_a_python_heredoc_does_not_leak_depth():
    """A Python `for i in range(200):` inside a heredoc in a shell tool has
    no `do` at command position, so it must not increment loop depth and
    silence the real shell `sleep` that follows it."""
    text = """#!/bin/bash
cat <<'PY' | python3 -
for i in range(200):
    print(i)
PY
sleep 10
"""
    found = tc.check_bare_sleep("ph-thing.sh", text)
    assert len(found) == 1


def test_audit_ranks_errors_before_warnings():
    class FakeTool(object):
        def __init__(self, name, text):
            self.name = name
            self.head = text
            self._text = text

        def read(self):
            return self._text

    warn_only = FakeTool("ph-warn.sh", "#!/bin/bash\nsleep 9\n")
    err_too = FakeTool("ph-err.sh", "#!/bin/bash\npkill -f x\nsleep 9\n")
    ranked = tc.audit([warn_only, err_too])
    assert [r["name"] for r in ranked] == ["ph-err.sh", "ph-warn.sh"]


def test_every_finding_has_a_severity_from_the_shared_set():
    """SEVERITIES is declared and meant to be the whole vocabulary a checker
    can use. Drive a fixture that trips all four checks at once and assert
    nothing escapes the set."""
    text = ("#!/bin/bash\n"
            "# exits: 0 ok · 3 see source\n"
            "pkill -f thing\n"
            "timeout 12 ssh \"$PHONE\" true\n"
            "sleep 30\n")
    found = []
    for check in tc.CHECKS:
        found.extend(check("ph-thing.sh", text))
    assert found, "fixture did not trip every checker"
    assert all(f.severity in tc.SEVERITIES for f in found)


def test_a_clean_tool_is_absent_from_the_audit():
    class FakeTool(object):
        name = "ph-clean.sh"

        def read(self):
            return "#!/bin/bash\n# exits: 0 ok \xb7 1 failed\necho hi\n"

    assert tc.audit([FakeTool()]) == []


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
