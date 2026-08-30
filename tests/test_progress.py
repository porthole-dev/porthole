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


# ------------------------------------------------- package builds --------

def test_ninja_states_a_total_so_the_fraction_is_not_a_guess():
    assert progress.ninja_progress("[123/4567] Building CXX object") == (123, 4567)


def test_pmbootstraps_timestamp_prefix_does_not_hide_ninja():
    """pmbootstrap relays every line as `[HH:MM:SS] ...`. Anchored matching
    against the raw line finds nothing, so the package bar would have read
    `unknown` for an entire webkit build and never said why."""
    assert progress.ninja_progress("[12:01:02] [4567/9999] Building") == (4567, 9999)


def test_a_bracketed_pair_in_prose_is_not_progress():
    assert progress.ninja_progress("note: see [3/4] in the manual") is None
    assert progress.ninja_progress("[11:28:24] Building 1 package") is None


def test_a_step_past_the_total_is_refused_rather_than_clamped():
    assert progress.ninja_progress("[9/4] rebuilding") is None


def test_the_phases_of_a_package_build_come_from_real_output():
    lines = [
        ("[11:28:24] => (1/1) edge/phoc: Installing dependencies", "deps"),
        ("[11:28:30] (native) install abuild gcc-aarch64", "deps"),
        ("[11:28:34] => edge/phoc: Building package (cross compiling)", "build"),
        ("[12:01:02] [4567/9999] Building CXX object", "build"),
        ("[12:44:10] >>> phoc: Entering fakeroot...", "package"),
    ]
    phase = ""
    for line, want in lines:
        phase = progress.pkg_phase_of(line, phase)
        assert phase == want, f"{line!r} -> {phase}, wanted {want}"


def test_cmake_configure_is_its_own_phase_so_the_bar_is_not_a_flat_zero():
    """~90s of cmake emits no [N/M] at all. Without a phase of its own the
    bar sits at 0% looking hung, which is why people kill working builds."""
    assert progress.pkg_phase_of("-- Configuring done (85.3s)", "deps") == "configure"


def test_a_package_fraction_needs_no_history_at_all():
    with tempfile.TemporaryDirectory() as run:
        tracker = progress.PkgTracker(run, "pkg:phoc")
        tracker.feed("[12:00:01] [50/200] Building CXX object")
        assert tracker.snapshot()["progress"] == 0.25


def test_before_ninja_starts_a_first_build_admits_it_does_not_know():
    with tempfile.TemporaryDirectory() as run:
        tracker = progress.PkgTracker(run, "pkg:phoc")
        tracker.feed("-- Configuring done (85.3s)")
        snap = tracker.snapshot()
        assert snap["progress"] is None and snap["phase"] == "configure"


def test_a_package_build_does_not_overwrite_the_kernel_builds_status():
    with tempfile.TemporaryDirectory() as run:
        progress.Tracker(run, "auto").publish(force=True)
        progress.PkgTracker(run, "pkg:phoc").publish(force=True)
        names = sorted(p.name for p in pathlib.Path(run).glob("*status.json"))
        assert names == ["build-status.json", "pkg-status.json"]


# ------------------------------------------------------------ liveness ---

def test_a_run_whose_process_is_gone_is_stale_not_running():
    snap = {"state": "running", "pid": 4242}
    assert progress.liveness(snap, alive=lambda _: False) == "stale"
    assert progress.liveness(snap, alive=lambda _: True) == "running"


def test_a_finished_run_is_never_drawn_as_a_bar():
    """The defect this fixes: after a build failed at 11:45, `status` kept
    printing `[>   ] starting ... eta 6s` for hours. `state failed` was in
    the output and lost the argument to a bar that looked like motion."""
    snap = {"rung": "auto", "state": "failed", "phase": "starting", "pid": 1,
            "elapsed": 0.4, "progress": None, "eta": 6, "started": 1.0}
    head, rows = progress.status_report(snap, now=7201.4)
    assert "[" not in head and "FAILED" in head
    assert "120m00s ago" in head
    assert not any(label == "eta" for label, _ in rows)


def test_a_live_run_still_gets_its_bar_and_eta():
    snap = {"rung": "auto", "state": "running", "phase": "make", "pid": 1,
            "elapsed": 30.0, "progress": 0.5, "eta": 30.0, "started": 1.0}
    head, rows = progress.status_report(snap, alive=lambda _: True)
    assert head.startswith("[") and "make" in head
    assert ("eta", "30s") in rows


def test_a_status_with_no_state_does_not_claim_to_be_running():
    head, _ = progress.status_report({"rung": "?"}, now=0)
    assert "unknown" in head


def test_a_watcher_renders_the_same_line_the_build_prints():
    """Two implementations of this line is how a watcher ends up disagreeing
    with the thing it is watching."""
    with tempfile.TemporaryDirectory() as run:
        tracker = progress.Tracker(run, "auto")
        assert tracker.line() == progress.line_of(tracker.snapshot())


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
