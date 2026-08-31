#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unified-diff arithmetic: the check that would have caught taimen 0199.

The fixtures are the real patch, and the two ways it was broken. `WELL_FORMED`
is the hunk as it stands in pmaports today; `LOST_LINE` is that hunk with the
blank `+` line deleted, which is the failure the port actually hit; `STRIPPED`
is the same line reduced to an empty string, which is what trailing-whitespace
stripping does and which a naive parser silently counts as context.

`TRUNCATED` and `NO_MARKER` are the two shapes that must NOT be reported as
malformed -- both were found by sweeping all 3461 patches in pmaports, and a
check that flags them is a check nobody will leave switched on.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_series as series                            # noqa: E402

WELL_FORMED = """\
--- a/drivers/media/platform/qcom/venus/core.c
+++ b/drivers/media/platform/qcom/venus/core.c
@@ -940,7 +940,9 @@
-\t.cp_start = 0,
-\t.cp_size = 0x70800000,
-\t.cp_nonpixel_start = 0x1000000,
-\t.cp_nonpixel_size = 0x24800000,
+\t/*
+\t * No cp regions: measured to change nothing for TZ or for decode,
+\t * and venus autoloads before the radios. See the venus driver
+\t * documentation for details.
+\t */
+
 \t.dma_mask = 0xddc00000 - 1,
 \t.fwname = "qcom/venus-4.4/venus.mbn",
 };
"""

# The reported breakage: the lone `+` blank line is gone, so the body has one
# fewer insertion than the header promises.
LOST_LINE = WELL_FORMED.replace("+\n", "")

# The same line stripped to nothing. It now reads as CONTEXT, so the old count
# gains one and the new count is unchanged -- a different arithmetic signature
# from LOST_LINE, and the one a naive parser misses.
STRIPPED = WELL_FORMED.replace("+\n", "\n")

# A hunk that stops short of its declared TRAILING context at end of file.
# cups-nostrip.patch does exactly this and builds. Must not be reported.
TRUNCATED = """\
--- a/Makedefs.in
+++ b/Makedefs.in
@@ -46,5 +46,5 @@
 # Installation programs...
 #
-INSTALL_BIN\t= @INSTALL@ -c @INSTALL_STRIP@
+INSTALL_BIN\t= @INSTALL@ -c
"""

# A context line whose leading space was stripped, so it starts with a bare
# tab. Two ACTIVE pmaports patches look like this and build. Warning, never
# fatal.
NO_MARKER = """\
--- a/usr/iscsiadm.c
+++ b/usr/iscsiadm.c
@@ -3263,7 +3263,8 @@
 \tint packet_size=32, ping_count=1;
 \tint do_discover = 0, sub_mode = -1;
\tint timeout = ISCSID_REQ_TIMEOUT;
+\tint argerror = 0;
 \tstruct sigaction sa_old;
 \tstruct sigaction sa_new;
"""


def _kinds(text):
    return sorted({kind for kind, _msg in series.scan(text)})


def test_well_formed_series_is_clean():
    assert series.scan(WELL_FORMED) == [], series.scan(WELL_FORMED)


def test_a_lost_insertion_is_malformed():
    assert _kinds(LOST_LINE) == ["malformed"], series.scan(LOST_LINE)


def test_a_stripped_insertion_is_malformed():
    # It reads as context, so the arithmetic breaks on the OLD side. Catching
    # only the LOST_LINE signature would miss the likelier mechanism.
    assert _kinds(STRIPPED) == ["malformed"], series.scan(STRIPPED)


def test_the_message_names_both_numbers():
    (kind, message), = series.scan(LOST_LINE)
    assert kind == "malformed"
    assert "-7/+9" in message and "-7/+8" in message, message


def test_truncated_trailing_context_is_not_reported():
    # Equal deficit on both sides, ended at a boundary. 19 patches in pmaports.
    assert series.scan(TRUNCATED) == [], series.scan(TRUNCATED)


def test_a_line_with_no_marker_is_stripped_not_malformed():
    assert _kinds(NO_MARKER) == ["stripped"], series.scan(NO_MARKER)


def test_the_stripped_message_names_the_line_and_the_mechanism():
    (kind, message), = series.scan(NO_MARKER)
    assert kind == "stripped"
    assert "leading space" in message, message


def test_an_implicit_single_line_count_parses():
    # `@@ -1 +1 @@` means one line each. Reading the absent count as 0 would
    # report every one-line hunk in the tree as malformed.
    one = "@@ -1 +1 @@\n-old\n+new\n"
    assert series.scan(one) == [], series.scan(one)


def test_no_newline_marker_is_not_counted():
    text = "@@ -1,1 +1,1 @@\n-old\n\\ No newline at end of file\n+new\n"
    assert series.scan(text) == [], series.scan(text)


def test_a_commit_message_before_the_diff_is_ignored():
    # format-patch puts prose above the diff, and prose contains lines that
    # start with - and +. Counting starts at the first @@, never before it.
    text = "Subject: [PATCH] a thing\n\n-not a diff line\n+nor this\n\n" + WELL_FORMED
    assert series.scan(text) == [], series.scan(text)


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
