#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ph-gesture-bench.py --client: the app's own frame rate, not phoc's.

The bench read the DPU vsync counter and called it the frame rate. That is
phoc's output rate -- it repaints every vsync while any client animates -- so
a browser scrolling at 30 fps and one presenting video at 15 fps both read
"60 fps, 0 jank" (porthole-dev/porthole#43). The client column parses the
app's own WAYLAND_DEBUG log, and this pins the two ways that parse can lie:
counting the wrong surface (a cursor commits far more often than a stalled
window is the shape of the mistake), and mis-reading a timestamp format that
libwayland changed in 1.22.

Fixtures are hand-written log lines rather than a capture, because what is
being checked is the arithmetic on them, not that a phone once emitted them.
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "tk_gesture_bench", ROOT / "tools" / "ph-gesture-bench.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


def wl(stamp, surface):
    return "[%s]  -> %s.commit()" % (stamp, surface)


def test_the_busiest_surface_wins_not_the_first_one():
    lines = [wl("1:00:00.000", "wl_surface#7")]                  # the cursor
    lines += [wl("1:00:00.%03d" % (n * 8), "wl_surface#7") for n in range(1, 6)]
    lines += [wl("1:00:00.%03d" % (n * 33), "wl_surface#12") for n in range(9)]
    surface, gaps, _ = bench.client_frames(lines)
    assert surface == "wl_surface#12", surface
    assert len(gaps) == 8, gaps


def test_intervals_are_milliseconds_and_jank_is_counted():
    # 25 fps: every interval is 40 ms, which is what the DPU column hid behind
    # a steady 16.6. The log's own resolution is 1 ms, so pick a cadence that
    # lands on it rather than asserting a tolerance around a rounding error.
    lines = [wl("0:00:%06.3f" % (n * 0.040), "wl_surface#3") for n in range(31)]
    _surface, gaps, _seq = bench.client_frames(lines)
    assert round(gaps[len(gaps) // 2], 3) == 40.0, gaps[:3]
    assert sum(1 for g in gaps if g > 33.0) == len(gaps) == 30


def test_the_pre_1_22_millisecond_stamp_parses_the_same():
    hms = [wl("0:00:%06.3f" % (n * 0.016), "wl_surface#3") for n in range(11)]
    ms = [wl("%.3f" % (n * 16.0), "wl_surface#3") for n in range(11)]
    a = [round(g, 3) for g in bench.client_frames(hms)[1]]
    b = [round(g, 3) for g in bench.client_frames(ms)[1]]
    assert a == b == [16.0] * 10, (a, b)


def test_presented_sequence_deltas_come_out_as_vsyncs_per_frame():
    # seq_lo is the sixth arg; a delta of 4 means the panel showed the same
    # client buffer four times -- 15 fps behind a 60 Hz DPU counter.
    lines = ["[0:00:00.000] wp_presentation_feedback#9.presented(0, 1, 2, 16666, 0, %d, 9)" % s
             for s in (100, 104, 108, 111)]
    assert bench.client_frames(lines)[2] == [4, 4, 3]


def test_a_log_with_nothing_in_it_reports_nothing_rather_than_a_rate():
    assert bench.client_frames(["nothing to see here"]) == (None, [], [])


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
