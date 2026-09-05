# SPDX-License-Identifier: MIT
"""The tool contract, as checks a machine can run.

WHY A MODULE AND NOT JUST TESTS
    `tests/test_tools.py::tools()` already imports `collect()` from the CLI so
    that the contract tests and `porthole tools` can never disagree about what
    a tool IS. The same argument applies to what the contract SAYS: the audit
    an agent reads and the gates CI runs must not become two opinions. So the
    checks live here, pure, and both consume them.

WHY THE AUDIT REPORTS BEFORE IT GATES
    Every check here has violations in the tree today. A check wired straight
    into CI turns the suite red on the day it is written and keeps it red
    until dozens of tools are fixed, which is how a gate gets deleted rather
    than satisfied. A class is reported by `porthole tools audit` until its
    count reaches zero, and gated by tests/test_tools.py in the commit that
    clears it.

WHY PURE FUNCTIONS OVER (name, text)
    A checker that reads files cannot be tested without a tree, and a checker
    tested against the live tree changes meaning whenever somebody edits a
    tool. Text in, findings out.
"""
from __future__ import annotations

import collections
import re

Finding = collections.namedtuple("Finding", "check severity detail")

SEVERITIES = ("error", "warn")

# lib/porthole_cli.py is the definition; this is the set a tool header may
# declare. Kept as literals rather than imported so that a checker cannot be
# broken by an import cycle -- test_exit_codes_match_the_cli below asserts the
# two never drift.
SHARED_EXITS = frozenset((0, 1, 64, 69, 75, 76, 124, 130))

_EXITS_FIELD = re.compile(r"^#\s*exits:\s*(.*)$", re.M)
_LEADING_INT = re.compile(r"^\s*(\d+)")


def check_exit_codes(name, text):
    """`exits:` must declare codes from the shared set, and must say what they
    mean. brain/laws/exit-codes-are-an-api.md is already a law; nothing
    enforced it, and five tools currently declare "see source", which is the
    precise opposite of an API."""
    m = _EXITS_FIELD.search(text)
    if not m:
        # A missing field is test_every_tool_declares_the_four_fields's job.
        # Reporting it here too makes the audit noisy and lets the two checks
        # drift apart.
        return []
    out = []
    for part in m.group(1).split("·"):
        part = part.strip()
        if not part:
            continue
        num = _LEADING_INT.match(part)
        if num and int(num.group(1)) not in SHARED_EXITS:
            out.append(Finding(
                "exit-code-not-shared", "error",
                "declares exit %s, which is not one of %s"
                % (num.group(1), " ".join(str(c) for c in sorted(SHARED_EXITS)))))
        if "see source" in part.lower():
            out.append(Finding(
                "exit-code-undocumented", "error",
                "%r says 'see source' -- that is not an API" % part))
    return out


_PKILL_F = re.compile(r"\bpkill\s+(-\w+\s+)*-\w*f")
_EXEMPT = re.compile(r"#\s*contract:\s*([a-z-]+)(.*)$")


def _exempted(line, tag):
    """An exemption is a marker in the source carrying a reason.

    In the source rather than an allowlist file because the reason has to be
    readable next to the code it excuses; an allowlist in another directory is
    a list nobody reads and everybody appends to.
    """
    m = _EXEMPT.search(line)
    if not m or m.group(1) != tag:
        return None
    return m.group(2).strip()


def check_pkill_pattern(name, text):
    """`pkill -f <pattern>` over ssh matches the command line CARRYING the
    pattern and kills its own session. tk-thermal.sh already carries a comment
    saying this costs an afternoon; it cost two probe runs while the design
    that replaces it was being written. It also misses grandchildren, and is
    unportable. Stopping the cgroup slice replaces every use."""
    out = []
    for line in text.splitlines():
        if not _PKILL_F.search(line):
            continue
        reason = _exempted(line, "pkill-ok")
        if reason is None:
            out.append(Finding(
                "pkill-pattern", "error",
                "`pkill -f` self-kills over ssh and misses grandchildren; "
                "stop the slice instead"))
        elif not reason:
            out.append(Finding(
                "pkill-pattern", "error",
                "`# contract: pkill-ok` must carry a reason"))
    return out


_FIXED_TIMEOUT = re.compile(r"\btimeout\s+\d+(\.\d+)?\s+(ssh|scp)\b")


def check_fixed_timeout(name, text):
    """A literal ceiling on a remote command cannot tell a slow tool from a
    wedged one, and cannot be raised by the caller who knows better. Eleven
    tools carry one. The design replaces them with PH_SILENCE (liveness) and
    PH_DEADLINE (ceiling) -- the shape the shared ssh options already use with
    ServerAliveInterval and ConnectTimeout. A variable ceiling is fine and is
    deliberately not flagged: it already has the escape hatch."""
    out = []
    for line in text.splitlines():
        if not _FIXED_TIMEOUT.search(line):
            continue
        reason = _exempted(line, "timeout-ok")
        if reason is None:
            out.append(Finding(
                "fixed-timeout", "warn",
                "a literal ssh/scp ceiling cannot tell slow from wedged; "
                "use PH_SILENCE and PH_DEADLINE"))
        elif not reason:
            out.append(Finding(
                "fixed-timeout", "warn",
                "`# contract: timeout-ok` must carry a reason"))
    return out


_SLEEP = re.compile(r"^\s*sleep\s+(\d+)")
_LOOP_OPEN = re.compile(r"^\s*(while|until|for)\b")
_LOOP_CLOSE = re.compile(r"^\s*done\b")


def check_bare_sleep(name, text):
    """brain/laws/poll-never-sleep.md: a fixed wait is wrong in both
    directions -- it wastes the time the thing did not need and calls a
    failure when it needed more.

    A WARNING, NOT AN ERROR, and exemptible. The law is about waiting for an
    event you could have polled for; a hardware settling delay is not that,
    and thirty tools contain one. An error here would be a gate that gets
    deleted rather than satisfied.

    Shell loops are explicitly delimited by do/done, so depth tracking is exact.
    A sleep inside a loop (depth > 0) is a poll interval and is left alone.
    A remaining limitation: a Python tool's `for` would increment a depth that
    no `done` ever decrements, which is harmless only because the `sleep`
    pattern is anchored shell syntax that Python's `time.sleep(...)` never matches.
    """
    lines = text.splitlines()
    out = []
    loop_depth = 0
    for line in lines:
        if _LOOP_OPEN.match(line):
            loop_depth += 1
        elif _LOOP_CLOSE.match(line):
            loop_depth = max(0, loop_depth - 1)

        m = _SLEEP.match(line)
        if not m:
            continue
        if loop_depth > 0:
            continue
        reason = _exempted(line, "sleep-ok")
        if reason is None:
            out.append(Finding(
                "bare-sleep", "warn",
                "a fixed wait is wrong in both directions; poll for the "
                "condition (brain/laws/poll-never-sleep.md)"))
        elif not reason:
            out.append(Finding(
                "bare-sleep", "warn",
                "`# contract: sleep-ok` must carry a reason"))
    return out


CHECKS = (check_exit_codes, check_pkill_pattern, check_fixed_timeout,
          check_bare_sleep)


def audit(tools):
    """Findings per tool, worst first.

    Ranked by error count then warning count then name, so the list is stable
    between runs -- an audit whose order changes for no reason is an audit
    nobody can diff.
    """
    rows = []
    for tool in tools:
        text = tool.read()
        found = []
        for check in CHECKS:
            found.extend(check(tool.name, text))
        if not found:
            continue
        errors = sum(1 for f in found if f.severity == "error")
        rows.append({"name": tool.name,
                     "errors": errors,
                     "warnings": len(found) - errors,
                     "findings": [f._asdict() for f in found]})
    rows.sort(key=lambda r: (-r["errors"], -r["warnings"], r["name"]))
    return rows
