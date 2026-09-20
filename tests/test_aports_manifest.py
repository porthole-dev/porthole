#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The manifest of what this port carries on top of stock.

A fork nobody lists is a fork nobody checks, and ph-pkgcheck.sh's own header
records what that costs: temp/phoc sat at 0.56.0 while the mirror moved to
0.57.0, apk took the newer stock build, and both GPU-reset patches vanished
with no message anywhere.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_aports_manifest as manifest                 # noqa: E402

CONF = """\
mesa
  upstream: main/mesa
  tier:     required
  why:      three a5xx patches
  forked:   26.1.6-r0
  commit:   abc1234

webkit2gtk-6.0
  upstream: community/webkit2gtk-6.0
  tier:     optional
  why:      multi-hour build

phoc
  upstream: main/phoc
  why:      GPU-reset patches
"""


def test_parse_reads_every_field():
    got = manifest.parse(CONF)
    assert got["mesa"]["upstream"] == "main/mesa"
    assert got["mesa"]["forked"] == "26.1.6-r0"
    assert got["mesa"]["commit"] == "abc1234"


def test_names_defaults_to_every_tier():
    assert manifest.names(manifest.parse(CONF)) == [
        "mesa", "webkit2gtk-6.0", "phoc"]


def test_a_block_with_no_tier_is_required():
    got = manifest.parse(CONF)
    assert manifest.names(got, "required") == ["mesa", "phoc"]
    assert manifest.names(got, "optional") == ["webkit2gtk-6.0"]


def test_problems_names_a_block_with_no_why():
    got = manifest.parse("mesa\n  upstream: main/mesa\n")
    assert any("mesa" in p and "why" in p for p in manifest.problems(got)), \
        manifest.problems(got)


def test_problems_rejects_an_unknown_tier():
    got = manifest.parse(
        "mesa\n  upstream: main/mesa\n  why: x\n  tier: someday\n")
    assert any("someday" in p for p in manifest.problems(got))


def test_a_clean_manifest_has_no_problems():
    assert manifest.problems(manifest.parse(CONF)) == []


def test_the_shipped_taimen_manifest_is_clean_and_lists_mesa():
    got = manifest.load(ROOT, "google-taimen")
    assert got, "profiles/google-taimen/aports.conf is missing or empty"
    assert manifest.problems(got) == []
    assert "mesa" in manifest.names(got, "required")
    # chromium does not open a window on this device; it must not be carried.
    assert "chromium" not in got


def test_verdict_safe_when_our_pkgrel_is_higher_at_the_same_pkgver():
    assert manifest.verdict("26.1.6", "14", "26.1.6", "0") == "safe"


def test_verdict_loses_when_upstream_pkgrel_catches_up():
    assert manifest.verdict("26.1.6", "14", "26.1.6", "14") == "loses"
    assert manifest.verdict("26.1.6", "2", "26.1.6", "9") == "loses"


def test_verdict_at_risk_the_moment_pkgver_moves():
    # This is the phoc failure, and the one a high pkgrel does NOT protect.
    assert manifest.verdict("26.1.6", "14", "26.2.0", "0") == "at-risk"
    assert manifest.verdict("26.1.6", "14", "26.1.7", "0") == "at-risk"


def test_verdict_positive_control_a_carried_fork_can_actually_lose():
    # A check that cannot fail proves nothing: assert the alarm fires for a
    # fork that IS outranked, not only that safe forks read safe.
    assert manifest.verdict("0.56.0", "60", "0.57.0", "0") == "at-risk"


def test_entry_text_records_where_the_fork_came_from():
    got = manifest.entry_text("gnome-calculator", "main/gnome-calculator",
                              "49.0", "1", "deadbee")
    parsed = manifest.parse(got)
    assert parsed["gnome-calculator"]["upstream"] == "main/gnome-calculator"
    assert parsed["gnome-calculator"]["forked"] == "49.0-r1"
    assert parsed["gnome-calculator"]["commit"] == "deadbee"


def test_a_fresh_entry_is_flagged_until_someone_says_why():
    # The whole value of the manifest is `why:`. A fork auto-recorded with a
    # placeholder must fail problems() so it cannot be quietly forgotten.
    got = manifest.parse(manifest.entry_text(
        "gnome-calculator", "main/gnome-calculator", "49.0", "1", "deadbee"))
    assert manifest.problems(got), "an unexplained fork must be a problem"


# -- a verdict computed against stale data is not a verdict ------------------
#
# 2026-09-20: drift reported `mesa 26.2.2-r51 upstream 26.2.2-r1 SAFE` and was
# right about the data it had. The data was eleven days old; Alpine had moved
# to 26.2.3, the phone had taken stock, and fifty-one releases of a5xx patches
# were not installed. The sync date was printed the whole time. A footnote and
# a verdict do not carry the same weight.

def test_a_clean_verdict_goes_stale_when_the_data_is_old():
    import porthole_aports_manifest as man

    assert man.stale_verdict("safe", 11) == "stale"


def test_a_fresh_clean_verdict_survives():
    import porthole_aports_manifest as man

    assert man.stale_verdict("safe", 1) == "safe"
    assert man.stale_verdict("safe", man.STALE_AFTER_DAYS) == "safe"


def test_a_problem_found_against_old_data_is_still_a_problem():
    """Upstream only moves forward, so an old comparison that found a fork
    losing has still found a real one. Only `safe` is downgraded."""
    import porthole_aports_manifest as man

    assert man.stale_verdict("loses", 99) == "loses"
    assert man.stale_verdict("at-risk", 99) == "at-risk"
    assert man.stale_verdict("unresolved", 99) == "unresolved"


def test_an_unknown_age_is_treated_as_stale():
    """Empty must mean unknown, never 'fresh enough'."""
    import porthole_aports_manifest as man

    assert man.stale_verdict("safe", -1) == "stale"
    assert man.age_in_days("") == -1
    assert man.age_in_days("not-a-date") == -1


def test_the_age_is_whole_days():
    import datetime

    import porthole_aports_manifest as man

    assert man.age_in_days("2026-09-09", datetime.date(2026, 9, 20)) == 11
    assert man.age_in_days("2026-09-20", datetime.date(2026, 9, 20)) == 0



if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
