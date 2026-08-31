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
import subprocess
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
    generic_names = [name for name, _ in caps.GENERIC]
    assert [n for n in names if n in set(generic_names)] == generic_names, names
    got = dict(merged)
    assert got["video-decode"]["present"] == "test -e /dev/video7"


def test_a_profile_can_add_a_capability_the_generic_table_lacks():
    merged = dict(caps.merge(caps.GENERIC, caps.parse("easel\n  present: test -e /dev/easel\n")))
    assert merged["easel"]["present"] == "test -e /dev/easel"


RS, US = "\x1e", "\x1f"


def test_script_emits_one_framed_record_per_probe():
    caps_list = [("wifi", {"present": "true", "works": "false"})]
    text = caps.script(caps_list)
    assert text.count("printf") >= 2, text
    assert "wifi" in text


def test_demux_reads_rc_and_output_per_cell():
    stream = (RS + "wifi" + US + "present" + US + "0" + US + "phy0"
              + RS + "wifi" + US + "works" + US + "1" + US + "")
    got = caps.demux(stream)
    assert got[("wifi", "present")]["rc"] == 0
    assert got[("wifi", "present")]["out"] == "phy0"
    assert got[("wifi", "works")]["rc"] == 1


def test_demux_ignores_anything_before_the_first_record():
    # A login banner, an MOTD, an ssh warning: none of it is a cell.
    stream = ("Welcome to postmarketOS\n" + RS + "wifi" + US + "present"
              + US + "0" + US + "phy0")
    got = caps.demux(stream)
    assert list(got) == [("wifi", "present")], got


def test_demux_keeps_the_first_record_when_the_leading_rs_was_stripped():
    """This is what Device.run() actually hands demux().

    `Device.run()` returns `proc.stdout.strip()`, and RS (`\\x1e`) is
    whitespace as far as `str.strip()` is concerned -- so on a real run the
    leading separator is gone before demux ever sees the text. A demux that
    assumed chunk[0] is always banner text threw away the first
    capability's first probe on every single run, silently: it rendered as
    `?`, indistinguishable from a probe that never ran.
    """
    stream = ("wifi" + US + "present" + US + "0" + US + "phy0"
              + RS + "wifi" + US + "works" + US + "1" + US + "")
    got = caps.demux(stream)
    assert len(got) == 2, got
    assert got[("wifi", "present")]["rc"] == 0, got


def test_demux_keeps_the_first_record_when_a_leading_rs_survives():
    stream = (RS + "wifi" + US + "present" + US + "0" + US + "phy0"
              + RS + "wifi" + US + "works" + US + "1" + US + "")
    got = caps.demux(stream)
    assert len(got) == 2, got
    assert got[("wifi", "present")]["rc"] == 0, got


def test_demux_drops_a_banner_but_keeps_every_record_including_the_first():
    stream = ("Welcome to postmarketOS\n" + RS + "wifi" + US + "present"
              + US + "0" + US + "phy0"
              + RS + "wifi" + US + "works" + US + "1" + US + "")
    got = caps.demux(stream)
    assert len(got) == 2, got
    assert got[("wifi", "present")]["rc"] == 0, got


def test_verdict_maps_rc_to_yes_and_no():
    assert caps.verdict({"rc": 0, "out": ""}) == "yes"
    assert caps.verdict({"rc": 1, "out": ""}) == "no"


def test_a_missing_record_is_unknown_not_no():
    """empty must mean unknown, never changed -- and never `no`.

    A probe that did not run and a probe that said no are different answers,
    and reporting the first as the second is how a matrix becomes a thing that
    lies confidently.
    """
    assert caps.verdict(None) == "?"


def test_a_capability_with_no_probe_for_that_field_is_unknown():
    assert caps.verdict({}) == "?"


def test_script_output_contains_the_real_separator_bytes():
    # A regression that dropped RS/US from the format string would still pass
    # a "printf appears twice" count -- assert the actual framing bytes.
    text = caps.script([("wifi", {"present": "true", "works": "false"})])
    assert RS in text, text
    assert US in text, text


def test_demux_drops_bad_rc_unknown_field_and_a_truncated_record():
    stream = (
        RS + "wifi" + US + "present" + US + "notanumber" + US + "junk"
        + RS + "wifi" + US + "not-a-field" + US + "0" + US + "junk"
        + RS + "wifi" + US + "works"
    )
    got = caps.demux(stream)
    assert got == {}, got


def test_a_syntax_error_in_one_probe_does_not_abort_later_capabilities():
    """The containment claim, proven against real bash, not just read.

    An earlier version spliced the command straight into `{ cmd ; }`, and a
    capability named with a stray apostrophe or a probe with an unbalanced
    `}` broke the OUTER script's parse, silently zeroing every capability
    after it. Passing each probe, name and field as one `shlex.quote()`-d
    argument to a nested `bash -c` turns that into a runtime failure of the
    inner shell only -- the outer script still parses and later capabilities
    still fire.
    """
    caps_list = [
        ("modem's-sim", {"present": "true"}),
        ("bad-probe", {"present": "echo hi; }; echo sneaky"}),
        ("wifi", {"present": "true", "works": "false"}),
    ]
    text = caps.script(caps_list)
    out = subprocess.run(["bash", "-c", text], capture_output=True,
                          text=True, timeout=10).stdout
    got = caps.demux(out)
    assert got[("wifi", "present")]["rc"] == 0, got
    assert got[("wifi", "works")]["rc"] == 1, got


def test_rows_pair_the_two_questions_and_carry_their_commands():
    import porthole_cmd_matrix as matrix

    merged = [("wifi", {"present": "iw dev", "works": "ip -4 addr"})]
    results = {("wifi", "present"): {"rc": 0, "out": "phy0"},
               ("wifi", "works"): {"rc": 0, "out": "inet 172.16.42.1"}}
    row, = matrix.rows(merged, results)

    assert row["name"] == "wifi"
    assert row["present"] == "yes" and row["works"] == "yes"
    assert row["present_cmd"] == "iw dev", row
    assert "172.16.42.1" in row["evidence"], row
    # rc IS rule 2's evidence -- "cites its probe" means the command AND
    # what it returned, not just the command.
    assert row["present_rc"] == 0 and row["works_rc"] == 0, row


def test_a_capability_present_but_not_working_reports_both_honestly():
    """The taimen wifi case: phy0 exists, association never happens.

    A matrix that collapses these two into one column is the matrix that lied
    for four sessions.
    """
    import porthole_cmd_matrix as matrix

    merged = [("wifi", {"present": "iw dev", "works": "ip -4 addr"})]
    results = {("wifi", "present"): {"rc": 0, "out": "phy0"},
               ("wifi", "works"): {"rc": 1, "out": ""}}
    row, = matrix.rows(merged, results)
    assert row["present"] == "yes" and row["works"] == "no", row


def test_an_untested_capability_is_question_mark_not_no():
    import porthole_cmd_matrix as matrix

    merged = [("bluetooth", {"present": "test -d /sys/class/bluetooth/hci0"})]
    results = {("bluetooth", "present"): {"rc": 0, "out": ""}}
    row, = matrix.rows(merged, results)
    assert row["present"] == "yes", row
    assert row["works"] == "?", row


def test_the_summary_never_counts_a_question_mark_as_working():
    import porthole_cmd_matrix as matrix

    rows = [{"name": "a", "present": "yes", "works": "yes"},
            {"name": "b", "present": "yes", "works": "?"},
            {"name": "c", "present": "no", "works": "no"}]
    summary = matrix.summarise(rows)
    assert summary["works"] == 1, summary
    assert summary["untested"] == 1, summary
    assert summary["total"] == 3, summary


def test_the_boot_refusal_comes_before_any_cache_write():
    """No stale cache from a refusal.

    A `?`-everywhere matrix cached from a not-BOOTED refusal would later read
    as "we looked and found nothing" -- exactly the lie this branch exists to
    prevent. Pinned via AST on the actual nodes, not by driving the verb
    (which would need a real or faked device): find the earliest `Bail(...)`
    call and the earliest `matrix.json` write, and assert the Bail comes
    first in the source.
    """
    import ast
    import inspect
    import textwrap
    import porthole_cmd_matrix as matrix

    source = inspect.getsource(matrix.cmd_matrix)
    tree = ast.parse(textwrap.dedent(source))

    bail_lines = []
    write_lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "Bail":
            bail_lines.append(node.lineno)
        elif (isinstance(node.func, ast.Attribute)
              and node.func.attr == "replace"):
            # os.replace(tmp, rundir / "matrix.json") -- the atomic
            # publish of the cache, not the tmp-file write itself.
            write_lines.append(node.lineno)

    # ast.walk is breadth-first, not source order, so the last node visited
    # is not the last in the file -- min() finds the EARLIEST occurrence of
    # each, which is the right question: does the first refusal happen
    # before the first cache write?
    bail_lineno = min(bail_lines) if bail_lines else None
    write_lineno = min(write_lines) if write_lines else None

    assert bail_lineno is not None, (
        "cmd_matrix must Bail() on a device that is not BOOTED")
    assert write_lineno is not None, (
        "cmd_matrix must publish matrix.json via os.replace()")
    assert bail_lineno < write_lineno, (
        "cmd_matrix must refuse a not-BOOTED device before writing "
        "matrix.json; a cache from a refusal reads as a look that found "
        "nothing")


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
