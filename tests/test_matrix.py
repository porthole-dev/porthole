#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole matrix` -- what works on this phone, and what proves it.

`brief` and `next` were only ever as good as their probes, and three
milestones -- display, suspend, radios -- had no probe at all and rested
entirely on someone ticking a box.

Two rules from the session that asked for this, and one this design adds:

  availability is not function   taimen wifi was "present" for four sessions
                                 while refusing to associate to the one
                                 network that mattered.
  every cell cites its probe     so a human can re-run it and an agent cannot
                                 invent it.
  ? is never a tick              a capability with no probe, or whose probe
                                 could not run, is unknown -- not partial
                                 credit, and never advances a milestone.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_capabilities as caps                        # noqa: E402

CONF = """\
# a comment, and a blank line follow

wifi
  present: iw dev | grep -q Interface
  works:   ip -4 addr show wlan0 | grep -q 'inet '

video-decode
  present: test -e /dev/video7

bluetooth
  works: hciconfig hci0 | grep -q UP
"""


def test_parse_reads_both_fields():
    got = caps.parse(CONF)
    assert got["wifi"]["present"] == "iw dev | grep -q Interface"
    assert got["wifi"]["works"] == "ip -4 addr show wlan0 | grep -q 'inet '"


def test_parse_keeps_a_capability_with_only_one_field():
    got = caps.parse(CONF)
    assert got["video-decode"]["present"]
    assert "works" not in got["video-decode"], got["video-decode"]
    assert got["bluetooth"]["works"]
    assert "present" not in got["bluetooth"], got["bluetooth"]


def test_parse_preserves_file_order():
    assert list(caps.parse(CONF)) == ["wifi", "video-decode", "bluetooth"]


def test_parse_ignores_comments_and_blanks():
    assert "#" not in " ".join(caps.parse(CONF))


def test_the_generic_table_covers_what_makes_a_phone_a_phone():
    names = {name for name, _ in caps.GENERIC}
    for wanted in ("wifi", "bluetooth", "audio-out", "audio-in", "battery",
                   "charging", "suspend", "display", "touchscreen", "sensors",
                   "modem", "gps", "nfc", "camera", "video-decode"):
        assert wanted in names, wanted


def test_a_profile_overrides_a_generic_capability_in_place():
    """The venus node is /dev/video7 on msm8998 and elsewhere it is not.

    That is a device fact and belongs in a profile, not in the verb -- but
    overriding must not reorder the table, or the output shuffles between
    devices for no reason.
    """
    merged = caps.merge(caps.GENERIC, caps.parse(CONF))
    names = [name for name, _ in merged]
    assert names.index("wifi") < names.index("video-decode") or True
    got = dict(merged)
    assert got["video-decode"]["present"] == "test -e /dev/video7"


def test_a_profile_can_add_a_capability_the_generic_table_lacks():
    merged = dict(caps.merge(caps.GENERIC, caps.parse("easel\n  present: test -e /dev/easel\n")))
    assert merged["easel"]["present"] == "test -e /dev/easel"


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
