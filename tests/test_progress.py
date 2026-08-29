#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build progress: the arithmetic behind a user-facing ETA.

A build used to be a black box, and agents filled the gap by inventing
`sleep 60` -- wrong in both directions, wasting forty seconds when the build
took twenty and calling a failure when it needed ninety. The fix is only worth
anything if the numbers it publishes are honest, so the honesty is what these
test: an unknown total says unknown, and an ETA is refused rather than
extrapolated from noise.
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_progress as progress  # noqa: E402


def test_a_build_with_no_history_admits_it_does_not_know():
    """The first run of a rung has nothing to predict from. A bar that invents
    a number reads as knowledge, which is worse than an empty one."""
    assert progress.fraction({}, "fast", elapsed=30, compile_seen=10) is None
    assert progress.eta({}, "fast", elapsed=30, frac=None) is None
    assert "?" in progress.bar(None)


def test_history_gives_a_fraction_from_the_compile_count():
    history = {"fast": {"total": 400, "compile_lines": 1000}}
    frac = progress.fraction(history, "fast", elapsed=100, compile_seen=250)
    assert abs(frac - 0.25) < 0.001, frac


def test_the_fraction_never_reaches_one_before_the_build_does():
    """A bar that sits at 100% while the build is still going is how people
    learn to distrust it. Below the baseline the ratio is honest."""
    history = {"fast": {"total": 400, "compile_lines": 1000}}
    assert progress.fraction(history, "fast", 100, 999) < 1.0


def test_overrunning_the_baseline_reads_as_unknown_not_as_almost_done():
    """Measured 2026-08-29: the stored baseline for `auto` was a small
    incremental run, so a 3879-step full rebuild passed it in ~5 s. Clamping to
    0.99 printed "99% eta 7s" for the remaining fourteen minutes of a 14m42s
    build. An exhausted baseline is not 99%, it is unknown."""
    history = {"auto": {"total": 44, "compile_lines": 6}}
    assert progress.fraction(history, "auto", elapsed=5, compile_seen=300) is None
    assert progress.eta(history, "auto", elapsed=300, frac=None) is None
    assert "?" in progress.bar(None)


def test_an_eta_is_not_reported_as_zero_once_the_last_total_is_passed():
    """`max(0.0, total - elapsed)` pinned a long build at "eta 0s" forever."""
    history = {"kernel": {"total": 600, "compile_lines": 0}}
    assert progress.eta(history, "kernel", elapsed=900, frac=None) is None


def test_elapsed_is_the_fallback_when_nothing_has_compiled_yet():
    history = {"kernel": {"total": 600, "compile_lines": 0}}
    frac = progress.fraction(history, "kernel", elapsed=300, compile_seen=0)
    assert abs(frac - 0.5) < 0.01, frac


def test_an_eta_is_refused_rather_than_extrapolated_from_noise():
    """At 2% done the ratio is noise, and a wildly wrong ETA is exactly what
    teaches people to ignore the field."""
    assert progress.eta({}, "fast", elapsed=5, frac=0.02) is None


def test_an_eta_falls_back_to_the_last_total():
    history = {"fast": {"total": 400, "compile_lines": 1000}}
    remaining = progress.eta(history, "fast", elapsed=100, frac=0.01)
    assert abs(remaining - 300) < 1, remaining


def test_phases_come_from_the_lines_the_script_already_prints():
    assert progress.phase_of("  CC drivers/gpu/drm/msm/msm_drv.o", "") == "make"
    assert progress.phase_of(">> flashing boot to both slots", "make") == "flash"
    assert progress.phase_of("pmbootstrap install --password x", "make") == "install"
    assert progress.phase_of("some unrelated noise", "make") == "make"


def test_a_later_phase_wins_when_a_line_mentions_two():
    """`>> verifying the exported image` names both export and verify, and the
    true phase is the later one."""
    assert progress.phase_of(">> verifying the exported image is what we built",
                             "install") == "verify"


def test_durations_read_as_durations():
    assert progress.fmt_dur(0) == "0s"
    assert progress.fmt_dur(41) == "41s"
    assert progress.fmt_dur(161) == "2m41s"
    assert progress.fmt_dur(None) == "--"


def test_history_survives_a_round_trip():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-progress-"))
    progress.record(tmp, "fast", 362.4, 1180)
    back = progress.load_history(tmp)
    assert back["fast"]["total"] == 362.4, back
    assert back["fast"]["compile_lines"] == 1180, back


def test_a_missing_history_file_is_not_an_error():
    """Bookkeeping must never be a way for a build to fail."""
    assert progress.load_history("/nonexistent/porthole/rundir") == {}


def test_the_tracker_publishes_something_an_agent_can_poll():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-progress-"))
    tracker = progress.Tracker(tmp, "fast")
    tracker.feed("  CC drivers/gpu/drm/msm/msm_drv.o\n")
    tracker.feed("  CC drivers/gpu/drm/msm/msm_gpu.o\n")
    tracker.publish(force=True)

    snap = json.loads((tmp / "build-status.json").read_text())
    for key in ("rung", "phase", "state", "elapsed", "progress", "eta",
                "last", "pid"):
        assert key in snap, (key, sorted(snap))
    assert snap["rung"] == "fast"
    assert snap["phase"] == "make", snap
    assert snap["state"] == "running"
    assert "msm_gpu.o" in snap["last"], snap["last"]


def test_finishing_records_what_the_rung_cost():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-progress-"))
    tracker = progress.Tracker(tmp, "mod")
    tracker.feed("  CC drivers/x.o\n")
    tracker.finish(ok=True)

    assert json.loads((tmp / "build-status.json").read_text())["state"] == "done"
    assert "mod" in progress.load_history(tmp), progress.load_history(tmp)


def test_a_failed_build_is_not_recorded_as_a_baseline():
    """A build that died after ten seconds must not become the estimate that
    every later build is measured against."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-progress-"))
    tracker = progress.Tracker(tmp, "kernel")
    tracker.finish(ok=False)
    assert "kernel" not in progress.load_history(tmp)
    assert json.loads((tmp / "build-status.json").read_text())["state"] == "failed"


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
