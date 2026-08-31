# SPDX-License-Identifier: MIT
"""Is this patch series actually applicable? Answered without a build.

WHY
    The taimen aport series contained one malformed patch, so NO aport kernel
    could build, and nothing said so. Work drifted to a tree that had none of
    the 19 venus patches, the port silently lost a whole subsystem, and the
    only symptom at the top was a missing /dev/video7. The series was
    discoverable as broken only by spending two minutes on a build that failed
    for a reason nothing surfaced.

WHY ARITHMETIC AND NOT `patch --dry-run`
    `patch` is not installed on the reference host, so shelling out to it was
    never the portable answer -- the same constraint that keeps this CLI free
    of dependencies. `git apply --check` needs the source tree at the right
    base, which is exactly what you do not have when the series is the broken
    thing, and it is lenient about the whitespace damage that caused this.
    A hunk header's arithmetic is checkable in milliseconds against a
    two-minute build.

WHAT THE CORPUS TAUGHT
    The first version of this flagged 28 of the 3461 patches in pmaports,
    which is a check nobody would leave switched on. Every one was examined.
    Three groups, and the severities below are what that measurement decided:

      tolerated  19 hunks end SHORT of their declared TRAILING context, at
                 end of file. `patch` matches on LEADING context and treats
                 trailing context as a hint, so they apply exactly as written
                 and build today. Silent.
      stripped   16 have a body line with no diff marker at all -- a context
                 line whose leading space was lost, so it begins with a bare
                 tab. Two are in ACTIVE aports and build. GNU patch often
                 still applies them. A warning that names the line, never a
                 refusal: blocking a rk322x porter on a patch that works is
                 how a check gets ignored.
      malformed  arithmetic that changes what would be applied. ZERO
                 instances in all 3461. That is what makes it safe to be
                 fatal, and it is the taimen defect exactly.
"""
from __future__ import annotations

import re

# The counts are optional in unified diff: `@@ -1 +1 @@` means one line each.
# Reading an absent count as 0 reports every one-line hunk in the tree.
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# What ends a hunk body. Deliberately NOT `--- ` or `+++ `: deleting a line
# that itself begins `-- ` produces `--- ` in the diff, which is
# indistinguishable from a file header, and treating it as one would miscount
# real patches. `diff --git` always precedes a real file header in anything
# git produced, and `-- ` alone is the signature separator.
_BOUNDARY = ("@@", "diff --git ")


def scan(text: str) -> list:
    """`(kind, message)` for every defect in one unified diff. Pure.

    `kind` is `malformed` (fatal: the arithmetic changes what gets applied) or
    `stripped` (a warning: a body line lost its diff marker).
    """
    lines = text.splitlines()
    found = []
    i = 0
    while i < len(lines):
        match = _HUNK.match(lines[i])
        if not match:
            i += 1
            continue
        header, at = lines[i], i + 1
        old = int(match.group(2)) if match.group(2) is not None else 1
        new = int(match.group(4)) if match.group(4) is not None else 1
        seen_old = seen_new = 0
        i += 1
        ended = "boundary"
        while i < len(lines):
            line = lines[i]
            if line.startswith(_BOUNDARY) or line == "-- ":
                break
            if line.startswith("\\"):
                pass                    # "\ No newline at end of file"
            elif line.startswith("+"):
                seen_new += 1
            elif line.startswith("-"):
                seen_old += 1
            elif line == "" or line.startswith(" "):
                # An empty line is a context line whose single trailing space
                # was stripped. Every mailer and half the editors in use do
                # this; refusing it would report most of the corpus.
                seen_old += 1
                seen_new += 1
            else:
                found.append((
                    "stripped",
                    "line {}: {!r} has no diff marker -- a context line whose "
                    "leading space was stripped".format(i + 1, line[:60])))
                ended = "stripped"
                break
            i += 1
            if seen_old >= old and seen_new >= new:
                ended = "counted"
                break
        if ended == "stripped":
            continue
        if (seen_old, seen_new) == (old, new):
            continue
        # A missing TRAILING context line costs one from each side. An unequal
        # deficit means a + or - line went missing from the middle, which
        # changes the result -- that is the taimen defect.
        short_old, short_new = old - seen_old, new - seen_new
        if ended == "boundary" and short_old == short_new > 0:
            continue
        found.append((
            "malformed",
            "{} (line {}): header says -{}/+{}, body has -{}/+{}".format(
                header, at, old, new, seen_old, seen_new)))
    return found
