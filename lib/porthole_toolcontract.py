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


CHECKS = (check_exit_codes,)
