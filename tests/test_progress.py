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
import os
import pathlib
import re
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

import porthole_progress as progress  # noqa: E402


def _at(stamp: str) -> float:
    """Local wall clock for `YYYY-MM-DD HH:MM:SS`.

    LOCAL, because pmbootstrap's `(pid) [HH:MM:SS]` is: the log carries a time
    of day and no date, and the day it belongs to is the reader's.
    """
    return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))


def test_a_build_with_no_history_admits_it_does_not_know():
    """The first run of a rung has nothing to predict from. A bar that invents
    a number reads as knowledge, which is worse than an empty one."""
    assert progress.fraction({}, "fast", elapsed=30, compile_seen=10) is None
    assert progress.eta({}, "fast", elapsed=30, frac=None) is None
    assert "unknown" in progress.bar(None)


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
    assert "unknown" in progress.bar(None)


def test_the_bar_does_not_reset_when_the_compile_count_takes_over():
    """Reported 2026-09-02 from the status line: `fast` climbed to 50% through
    pmbootstrap's quiet prologue on elapsed-against-last-total, then the first
    `CC` line arrived, the compile count took over at 1/4335, and the bar read
    0%. A bar that resets is read as a build that restarted. Unknown until the
    thing being counted starts."""
    history = {"fast": {"total": 400, "compile_lines": 4335}}
    assert progress.fraction(history, "fast", elapsed=200, compile_seen=0) is None
    assert "unknown" in progress.bar(None)
    # ...and once it starts, it is the compile count and nothing else.
    frac = progress.fraction(history, "fast", elapsed=200, compile_seen=4335 // 2)
    assert abs(frac - 0.5) < 0.01, frac


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


def test_the_install_step_ph_build_announces_resolves_to_install():
    """ph-build.sh's `pmbootstrap install` runs as a plain shell command and
    is never echoed, so `_MARKERS`' `pmbootstrap install` alternative never
    fires on it -- only the `>>` announcement tkbuild() prints right before
    it does. Coupled to the exact string in tools/ph-build.sh's tkbuild() so
    this breaks if one is reworded without the other."""
    assert progress.phase_of(
        ">> installing the kernel into the rootfs chroot (pmbootstrap install) --",
        "package") == "install"


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
    # These builds are hours. `134m10s` is a number you have to do arithmetic
    # on before it tells you anything.
    assert progress.fmt_dur(8050) == "2h14m"


def test_history_survives_a_round_trip():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-progress-"))
    progress.record(tmp, "fast", 362.4, 1180)
    back = progress.load_history(tmp)
    assert back["fast"]["total"] == 362.4, back
    assert back["fast"]["compile_lines"] == 1180, back


def test_a_missing_history_file_is_not_an_error():
    """Bookkeeping must never be a way for a build to fail."""
    assert progress.load_history("/nonexistent/porthole/rundir") == {}


def test_history_key_separates_a_rebuild_from_a_cached_run():
    assert progress.history_key("fast", True) != progress.history_key("fast", False)
    assert "fast" in progress.history_key("fast", True)


def test_an_unkeyed_legacy_entry_is_not_read_as_either_bucket():
    """The old `fast: 403.4` is the mean of a 6m path and a 21m one.

    Seeding either bucket with it reintroduces the exact error this removes,
    once, in the bucket where nobody would look for it. No history is the
    honest answer, and `eta unknown` on a first run is already how this module
    behaves for a rung it has never seen.
    """
    history = {"fast": {"total": 403.4, "compile_lines": 0}}
    assert progress.estimate_total(history, progress.history_key("fast", True)) is None
    assert progress.estimate_total(history, progress.history_key("fast", False)) is None


def test_each_bucket_learns_its_own_total():
    rundir = pathlib.Path(tempfile.mkdtemp(prefix="porthole-hist-"))
    progress.record(rundir, progress.history_key("fast", True), 1284.0, 8100)
    progress.record(rundir, progress.history_key("fast", False), 403.4, 0)
    history = progress.load_history(rundir)

    assert progress.estimate_total(history, progress.history_key("fast", True)) == 1284.0
    assert progress.estimate_total(history, progress.history_key("fast", False)) == 403.4


def test_apk_is_current_is_false_when_the_release_apk_is_absent():
    import porthole_cmd_build as build

    packages = pathlib.Path(tempfile.mkdtemp(prefix="porthole-pkgs-")) / "aarch64"
    packages.mkdir(parents=True)
    assert not build.apk_is_current(packages.parent, "linux-x", "7.2.2", "22",
                                    "aarch64")
    (packages / "linux-x-7.2.2-r22.apk").write_text("")
    assert build.apk_is_current(packages.parent, "linux-x", "7.2.2", "22",
                               "aarch64")


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
    assert "2h00m ago" in head, head
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


# ------------------------------------------------- an ETA worth believing --
#
# Both cases below are MEASURED, from the webkit rebuild. See
# docs/HANDOFF-package-builds.md, "[N/M] alone will lie".

_LINES = {"compile": "[{}/{}] Building CXX object Source/WebCore/x.o",
          "generate": "[{}/{}] Generating DerivedSources/LLIntAssembly.h"}


def _driven(steps, total=8233, configure_secs=360):
    """A PkgTracker fed real ninja lines on a synthetic clock.

    The lines go through `feed()` so the parsing, the phase and the step kind
    are all the production path; only the clock is faked, by rewriting the
    sample timestamps afterwards. A fixture that sets the counters directly
    would pass while `feed()` was broken.
    """
    import time as _t
    tracker = progress.PkgTracker(tempfile.mkdtemp(), "pkg:webkit2gtk-6.0")
    span = sum(dt for dt, _ in steps)
    tracker.started = _t.time() - span - configure_secs
    now, n, stamps = _t.time() - span, 2600, []
    for dt, kind in steps:
        now += dt
        n += 1
        tracker.feed("[12:01:02] " + _LINES[kind].format(n, total))
        if kind == "compile":
            stamps.append((now, tracker.compiles))
    tracker._samples.clear()
    tracker._samples.extend(stamps)
    return tracker


def test_a_generator_stall_refuses_an_eta_instead_of_saying_74_hours():
    """Measured: the counter moved five steps in 240s while one emulated Ruby
    process ran JavaScriptCore's offlineasm, fifteen cores idle. Extrapolating
    that says 74 hours on a perfectly healthy build, and an agent watching
    that number kills it. The honest answer is that we do not know."""
    assert _driven([(48.0, "generate")] * 5)._eta(0.32) is None


def test_a_stall_is_shown_as_generating_rather_than_as_a_stuck_build():
    snap = _driven([(48.0, "generate")] * 5).snapshot()
    assert "generating" in progress.line_of(snap)
    assert "eta      --" in progress.line_of(snap)


def test_a_steady_compile_rate_does_produce_an_eta():
    """The refusal must not be so cautious that the field is never populated:
    a bar that never shows an ETA is the black box this replaced."""
    tracker = _driven([(1.25, "compile")] * 400)
    remaining = tracker.ninja_total - tracker.compile_seen
    assert abs(tracker._eta(0.32) - remaining / 0.8) < remaining * 0.05


def test_the_rate_ignores_time_spent_not_compiling():
    """Measured from the FIRST COMPILE, not from the start. Folding in six
    minutes of cmake configure reports a compile rate several times lower
    than the real one, and an ETA to match."""
    slow = _driven([(1.25, "compile")] * 400, configure_secs=3600)._eta(0.32)
    fast = _driven([(1.25, "compile")] * 400, configure_secs=0)._eta(0.32)
    assert abs(slow - fast) < 1.0, (slow, fast)


def test_no_eta_at_all_during_the_opening_minutes():
    """The first fifteen minutes of a resumed build are ccache replaying
    already-compiled objects -- 2658 of them on the measured run. Any rate
    taken there is measuring cache lookups."""
    tracker = _driven([(0.3, "compile")] * 200, configure_secs=0)
    tracker.started = tracker.started + 1e9  # elapsed ~0
    assert tracker._eta(0.32) is None


def test_a_window_with_too_few_compiles_is_discarded_not_divided_by():
    now = 1000.0
    assert progress.window_rate([(now - 200, 0), (now, 3)], now) is None
    assert progress.window_rate([(now - 100, 0), (now, 90)], now) == 0.9


def test_a_window_too_short_to_mean_anything_is_refused():
    now = 1000.0
    assert progress.window_rate([(now - 5, 0), (now, 40)], now) is None


def test_a_kernel_rung_prefers_a_measured_rate_over_the_history_baseline():
    """The windowed rate must actually win when there is one. Chosen so the
    two paths disagree: the window says 8000s, the history baseline says
    1200s. Asserting `is not None` would pass under either, which is how an
    earlier version of this test failed to notice _eta was never wired to
    window_rate at all."""
    import time as _t

    with tempfile.TemporaryDirectory() as run:
        tracker = progress.Tracker(run, "kernel")
        tracker.history = {"kernel": {"total": 600.0, "compile_lines": 5000}}
        tracker.started = _t.time() - 300
        tracker.compile_seen = 1000
        now = _t.time()
        tracker._samples.clear()
        # 30 samples over 116s, 2 compiles apart: rate 0.5/s, comfortably past
        # RATE_MIN_SPAN and RATE_MIN_STEPS.
        tracker._samples.extend(
            [(now - 116 + i * 4, 800 + i * 2) for i in range(30)])
        # (5000 - 1000) remaining / 0.5 per second.
        assert tracker._eta(0.2) == 8000.0, tracker._eta(0.2)


def test_a_kernel_rung_falls_back_to_history_when_the_window_is_too_thin():
    """The fallback must survive. A first run has no samples at all, and
    removing the history path would leave it with no ETA -- which is the
    black box this whole module replaced. 300/0.2 - 300 = 1200."""
    import time as _t

    with tempfile.TemporaryDirectory() as run:
        tracker = progress.Tracker(run, "kernel")
        tracker.history = {"kernel": {"total": 600.0, "compile_lines": 5000}}
        tracker.started = _t.time() - 300
        tracker.compile_seen = 1000
        now = _t.time()
        tracker._samples.clear()
        # Four samples across four minutes: span is long enough but only three
        # steps, below RATE_MIN_STEPS, so the window is discarded.
        tracker._samples.extend([(now - 240, 997), (now - 160, 998),
                                 (now - 80, 999), (now, 1000)])
        # Exact equality is flaky here: elapsed is live wall-clock time,
        # and microseconds pass between setting `started` and calling
        # `_eta`, so the raw value is 1200.00003... not 1200.0 exactly.
        assert abs(tracker._eta(0.2) - 1200.0) < 0.01, tracker._eta(0.2)


def test_a_build_with_nothing_to_do_says_so():
    """`elapsed 2s` looked incoherent because nothing explained it. The log's
    FIRST line said `Package 'gst-plugins-good' is up to date` and status kept
    only the last (`DONE!`), which carries no information. pmbootstrap states
    the reason outright, so record it rather than leave it to be inferred."""
    phase = progress.pkg_phase_of(
        "[16:00:07] NOTE: Package 'gst-plugins-good' is up to date", "")
    assert phase == "up-to-date"
    _, rows = progress.status_report(
        {"rung": "pkg:gst-plugins-good", "phase": "up-to-date", "state": "done",
         "pid": 1, "elapsed": 2.5, "started": 1000.0}, now=1003.0)
    assert dict(rows)["phase"] == "up-to-date"


def test_a_finished_run_does_not_claim_to_be_starting():
    """Reported from a real status: `state done` printed next to `phase
    starting`. The two-second build's only output was `DONE!`, which matched
    no phase marker, so the field never advanced -- and snapshot() had baked
    the RENDERING default "starting" into the stored data, where it then
    outlived the run."""
    done = {"rung": "pkg:gst-plugins-good", "phase": "", "state": "done",
            "pid": 1, "elapsed": 2.0, "started": 1000.0, "last": "DONE!"}
    _, rows = progress.status_report(done, now=1002.0)
    phase = dict(rows)["phase"]
    assert phase == "none reached", phase
    assert "starting" not in phase


def test_a_status_file_written_before_the_fix_reads_correctly_too():
    """The reported symptom was in a status file already on disk. Fixing only
    what snapshot() writes would leave it on screen until the next build."""
    done = {"rung": "pkg:gst-plugins-good", "phase": "starting",
            "state": "done", "pid": 1, "elapsed": 2.0, "started": 1000.0}
    _, rows = progress.status_report(done, now=1002.0)
    assert dict(rows)["phase"] == "none reached"


def test_the_snapshot_stores_the_raw_phase_not_the_display_default():
    """Presentation defaults belong in renderers. Storing "starting" is what
    made it survive the run that never left it."""
    with tempfile.TemporaryDirectory() as run:
        assert progress.Tracker(run, "auto").snapshot()["phase"] == ""


def test_a_live_run_still_reads_as_starting_before_any_phase_lands():
    """The default is still right where it belongs -- on the live view."""
    live = {"rung": "auto", "phase": "", "state": "running", "pid": 1,
            "elapsed": 1.0, "progress": None, "eta": None, "started": 1000.0}
    head, _ = progress.status_report(live, alive=lambda _: True)
    assert "starting" in head
    assert "starting" in progress.line_of(live)


def test_status_report_labels_a_stale_last_line_with_its_age():
    """`last DONE!` beside a stalled bar read as "finished and hung".

    It was pmbootstrap finishing a sub-step twenty minutes earlier, and the
    build was healthy. A stale line labelled stale is informative; unlabelled
    it is a lie.
    """
    now = 2000.0
    snap = {"rung": "fast", "state": "running", "pid": os.getpid(),
            "elapsed": 719.0, "progress": None, "eta": None,
            "compile_lines": 0, "last": "[14:49:08] DONE!",
            "last_at": now - 1200, "started": now - 719}
    _head, rows = progress.status_report(snap, now=now)
    last = dict(rows)["last"]
    assert "20m" in last, last


def test_stall_note_names_packaging_as_the_reason_for_no_signal():
    note = progress.stall_note("[14:49:08] DONE!", silence=1200.0)
    assert note, "a silent packaging phase must say why"
    assert "packag" in note.lower() or "compress" in note.lower(), note


def test_stall_note_is_silent_when_output_is_recent():
    assert progress.stall_note("  CC  drivers/media/x.o", silence=3.0) == ""


def test_line_of_carries_the_stall_note_watch_actually_renders():
    """`stall_note` had exactly one caller -- `status_report` -- reached only
    after a run has already stopped. `watch` renders `line_of` while the run
    is still live, so a healthy multi-minute `pmbootstrap install` looked
    identical to a hang there. The note belongs on the line `watch` draws."""
    stale = {"rung": "kernel", "phase": "install", "state": "running",
             "pid": 1, "elapsed": 133.0, "progress": None, "eta": None,
             "last": "Executing postmarketos-base-systemd-91-r1.trigger",
             "last_age": 133.0, "last_at": 1000.0}
    # `now` pinned: the age comes from `last_at` now, so a fixture that left
    # it to the wall clock would be asking about 1970.
    line = progress.line_of(stale, now=1133.0)
    assert "\n" not in line
    assert "no output for" in line or "install" in line.lower(), line


def test_line_of_stays_quiet_on_a_healthy_fast_moving_build():
    fresh = {"rung": "kernel", "phase": "make", "state": "running",
             "pid": 1, "elapsed": 133.0, "progress": 0.4, "eta": 10.0,
             "last": "  CC  drivers/gpu/drm/msm/msm_drv.o",
             "last_age": 2.0, "last_at": 1000.0}
    line = progress.line_of(fresh, now=1002.0)
    assert "\n" not in line
    assert line == progress.line_of(dict(fresh, last=""), now=1002.0)


def test_feed_advances_last_at():
    """`__init__` already sets `last_at = started`, so a test that only
    checks the field's PRESENCE (its previous shape) passes whether or not
    `feed()` ever touches it -- proven by deleting `self.last_at =
    time.time()` from `feed()` and re-running: 59/59 still passed. Prove the
    UPDATE instead: hold the clock at a fixed instant, snapshot, advance it,
    `feed()` a line, snapshot again, and check the value moved to the new
    instant, not merely that it is a float.
    """
    real_time = progress.time.time
    now = [1000.0]
    progress.time.time = lambda: now[0]
    try:
        tracker = progress.Tracker(
            pathlib.Path(tempfile.mkdtemp(prefix="porthole-t-")), "fast")
        before = tracker.snapshot()["last_at"]
        assert before == 1000.0, before
        now[0] = 1090.0
        tracker.feed("  CC  drivers/x.o\n")
        after = tracker.snapshot()
        assert after["last_at"] == 1090.0, after
        assert after["last_age"] == 0.0, after
    finally:
        progress.time.time = real_time


def test_status_report_pairs_a_stall_pattern_with_its_why_row():
    """The deliverable is "a `[??????]` bar always comes with a reason" --
    `stall_note` and `status_report` tested in isolation does not prove they
    are actually wired together. This is the join: a RUNNING snapshot whose
    `last` matches a stall pattern and is old enough gets a `why` row, and
    the same snapshot with a FRESH `last` gets none -- so the row cannot be
    unconditional."""
    now = 5000.0
    stale = {"rung": "fast", "state": "running", "pid": os.getpid(),
             "elapsed": 1300.0, "progress": None, "eta": None,
             "compile_lines": 0, "last": "[14:49:08] DONE!",
             "last_at": now - 1200, "started": now - 1300}
    _head, rows = progress.status_report(stale, alive=lambda _: True, now=now)
    rows = dict(rows)
    assert "why" in rows, rows
    assert "packag" in rows["why"].lower() or "compress" in rows["why"].lower()

    fresh = dict(stale, last="  CC  drivers/media/x.o", last_at=now - 3)
    _head, rows = progress.status_report(fresh, alive=lambda _: True, now=now)
    assert "why" not in dict(rows), dict(rows)


# --------------------------------------------------------- watch() (Task 13) --
#
# Moved here from `porthole_cmd_pkg._watch` so `porthole build watch` (Task
# 14) can be the same implementation. Every fixture below uses a FRESH
# timestamp (finished a few seconds after `now`, not before) -- a snapshot
# timestamped before the watch began is deliberately treated as somebody
# else's earlier run (decision 3) and left waiting for a NEW one, which is
# correct but would make these tests wait out the 30s ceiling. A fixture
# timestamped moments after "now" models watching a run that finished while
# we were attached to it, which is the case these tests are about.


def test_watch_returns_promptly_for_a_run_that_finished_moments_ago():
    """A `done` run discovered the instant `watch` starts must not be mistaken
    for a stale run left over from a previous watch session (decision 3) --
    it finished AFTER this watch began, so the report is immediate, not a
    30-second wait for a "new" run that will never come."""
    with tempfile.TemporaryDirectory() as rundir:
        now = time.time()
        snap = {"rung": "pkg:phoc", "state": "done", "pid": 1,
                "started": now, "elapsed": 5.0}
        (pathlib.Path(rundir) / "x-status.json").write_text(json.dumps(snap))
        lines = []
        rc = progress.watch(rundir, "x-status.json", 0.01, lines.append,
                            tty=False)
        assert rc == progress.EX_OK
        assert any("phoc" in line for line in lines)


def test_watch_reports_a_failed_run_as_nonzero():
    with tempfile.TemporaryDirectory() as rundir:
        now = time.time()
        snap = {"rung": "pkg:phoc", "state": "failed", "pid": 1,
                "started": now, "elapsed": 5.0}
        (pathlib.Path(rundir) / "x-status.json").write_text(json.dumps(snap))
        rc = progress.watch(rundir, "x-status.json", 0.01, [].append,
                            tty=False)
        assert rc == progress.EX_FAIL


def test_watch_detects_a_dead_pid_as_stale_instead_of_polling_forever():
    """A `running` snapshot whose process has already died must be caught by
    `liveness()` and reported, not polled at `interval` forever waiting for a
    pid that will never move again. Regression guard: real time spent here
    must stay well under a second."""
    with tempfile.TemporaryDirectory() as rundir:
        now = time.time()
        snap = {"rung": "pkg:phoc", "state": "running", "pid": 999999,
                "started": now, "elapsed": 5.0}
        (pathlib.Path(rundir) / "x-status.json").write_text(json.dumps(snap))
        started = time.time()
        rc = progress.watch(rundir, "x-status.json", 0.01, [].append,
                            tty=False)
        assert time.time() - started < 2.0, (
            "watch polled a dead pid instead of detecting it as stale")
        assert rc == progress.EX_FAIL


# Keys an agent actually consumes from an ndjson line, regardless of which
# branch of `watch` produced it. Shared between the live-path and the
# waiting-path test below ON PURPOSE: Task 14's review found the two
# branches emitting two different shapes ({rung, phase, ...} live vs.
# {state, note, previous} waiting), so `obj["rung"]` KeyErrored on line one
# in exactly the "somebody else's run just finished" case this feature
# exists to handle. If these two tests ever assert different key tuples, the
# union is back and neither test would catch it.
#
# `note` is in this tuple ON PURPOSE, after a SECOND review round found it
# present on waiting objects and absent from live ones -- a smaller version
# of the same defect, missed the first time because this constant did not
# name every key the contract promises. A constant that omits a key pins
# nothing about that key.
#
# `last_at`/`last_age` (Task 16 fix round 1) are in it for the same reason:
# they are the newest keys `snapshot()` grows, so they are the most likely
# pair to drift between the live and waiting branches next, and the only
# thing that had been checking they matched was a one-off script run by
# hand. Adding them here forced the two hand-written fixtures below to carry
# them too -- that churn is this constant doing its job.
NDJSON_KEYS_AN_AGENT_READS = ("rung", "phase", "state", "elapsed", "progress",
                              "eta", "last_at", "last_age", "note")


def test_watch_ndjson_streams_json_and_skips_the_summary_block():
    """An agent can consume a stream; it cannot consume a redrawn terminal
    (this is what `ndjson=True` is for). One JSON object per update, and none
    of the plain-text kv summary a human-facing watch prints at the end --
    proven by every emitted line parsing as JSON, which that summary does
    not.

    The fixture is shaped like `Tracker.snapshot()` actually writes it
    (porthole_progress.py Tracker.snapshot), not a hand-picked subset of
    keys -- the ndjson object is the agent-facing contract Task 14 depends
    on, so the schema has to come from production, not from the test.

    This is the LIVE branch: `started + elapsed` lands AFTER this watch
    began, so the run is not stale and `watch` reports it directly rather
    than waiting. See `test_watch_ndjson_waiting_path_carries_the_same_keys`
    for the other branch."""
    with tempfile.TemporaryDirectory() as rundir:
        now = time.time()
        snap = {"rung": "pkg:phoc", "phase": "build", "state": "done",
                "pid": 1, "elapsed": 5.0, "progress": 1.0, "eta": 0.0,
                "compile_lines": 42, "last": "DONE!", "last_at": now,
                "last_age": 0.0, "started": now}
        (pathlib.Path(rundir) / "x-status.json").write_text(json.dumps(snap))
        lines = []
        rc = progress.watch(rundir, "x-status.json", 0.01, lines.append,
                            ndjson=True, tty=False)
        assert rc == progress.EX_OK
        assert lines, "ndjson mode must not be silent"
        for line in lines:
            obj = json.loads(line)
        # "progress"/"eta" can be None there (an honest "unknown", not a
        # missing key), so only PRESENCE is asserted, not type.
        for key in NDJSON_KEYS_AN_AGENT_READS:
            assert key in obj, f"{key!r} missing from the ndjson object"
        # A live object has nothing to SAY -- the rest of its fields already
        # speak for it -- so `note` is present but empty, never absent.
        assert obj["note"] == "", obj


def test_watch_ndjson_waiting_path_carries_the_same_keys():
    """The branch that used to be the discriminated union's other shape.

    A snapshot that finished well BEFORE this watch began is stale --
    `is_stale` -- so `watch` never reports it as live; it emits "waiting"
    objects instead and (off a tty, ceiling patched down like the ceiling
    test below) gives up once the ceiling passes, still reporting the stale
    run's own exit code. Before this fix those waiting objects were
    `{"state": ..., "note": ..., "previous": {...}}` -- a consumer doing
    `json.loads(line)["rung"]` KeyErrored on line one, in precisely this
    "somebody else's run just finished" scenario. Now every field a
    snapshot carries is copied up to the top level, so the same key check as
    the live-path test above passes here too.
    """
    real_ceiling = progress.wait_ceiling
    progress.wait_ceiling = lambda tty, now, seconds=30.0: now + 0.05
    try:
        with tempfile.TemporaryDirectory() as rundir:
            finished = time.time() - 120  # long before this watch begins
            snap = {"rung": "pkg:phoc", "phase": "build", "state": "done",
                    "pid": 1, "elapsed": 5.0, "progress": 1.0, "eta": 0.0,
                    "compile_lines": 42, "last": "DONE!", "last_at": finished,
                    "last_age": 0.0, "started": finished - 5.0}
            (pathlib.Path(rundir) / "x-status.json").write_text(json.dumps(snap))
            lines = []
            rc = progress.watch(rundir, "x-status.json", 0.01, lines.append,
                                ndjson=True, tty=False)
    finally:
        progress.wait_ceiling = real_ceiling
    assert rc == progress.EX_OK, "the stale run's own state still decides the exit code"
    assert lines, "ndjson mode must not be silent while waiting either"
    for line in lines:
        obj = json.loads(line)
        assert obj["state"] == "waiting", obj
        assert "note" in obj, "the human sentence must still be reachable"
        for key in NDJSON_KEYS_AN_AGENT_READS:
            assert key in obj, f"{key!r} missing from the waiting ndjson object"


def test_watch_ndjson_waiting_path_is_all_none_when_nothing_ever_published():
    """No file has EVER been written -- there is no previous snapshot to copy
    fields from at all. The key set must still hold, with `None` in every
    slot rather than the keys simply being absent."""
    real_ceiling = progress.wait_ceiling
    progress.wait_ceiling = lambda tty, now, seconds=30.0: now + 0.05
    try:
        with tempfile.TemporaryDirectory() as rundir:
            lines = []
            try:
                progress.watch(rundir, "x-status.json", 0.01, lines.append,
                               ndjson=True, tty=False)
            except progress.Bail:
                pass  # expected once the ceiling passes; the point is `lines`
    finally:
        progress.wait_ceiling = real_ceiling
    assert lines, "ndjson mode must not be silent while waiting"
    for line in lines:
        obj = json.loads(line)
        assert obj["state"] == "waiting", obj
        for key in NDJSON_KEYS_AN_AGENT_READS:
            assert key in obj, f"{key!r} missing from the waiting ndjson object"
        assert obj["rung"] is None, "nothing has ever published -- must be null, not guessed"


def test_watch_says_something_before_the_first_sleep_and_gives_up_on_a_ceiling():
    """No status file has ever been written. Off a tty this must not hang --
    the real ceiling default is 30s, patched down here so the test stays
    fast -- and it must have said SOMETHING before giving up, never the old
    bare `continue`'s blank screen."""
    real_ceiling = progress.wait_ceiling
    progress.wait_ceiling = lambda tty, now, seconds=30.0: now + 0.05
    try:
        with tempfile.TemporaryDirectory() as rundir:
            lines = []
            try:
                progress.watch(rundir, "x-status.json", 0.01, lines.append,
                               tty=False)
                raise AssertionError("expected Bail: nothing ever published")
            except progress.Bail as exc:
                assert exc.message == "no x run has published a status here"
                assert "porthole x watch" in exc.hint
        assert lines, "must say something before the ceiling, never silent"
    finally:
        progress.wait_ceiling = real_ceiling


# ------------------------------------------- what `watch` actually paints --
#
# Reported, verbatim: "it's been stale full of ??? and info like package --
# this watch is pretty much useless from a watching pov, the developer should
# receive much more real time and not stale information". The observed render,
# unchanged for minutes:
#
#     [??????????????????] -- push         4m16s eta      --
#
# while the status file it was reading held `>> reboot 1/4: burning a boot
# retry (boot_id c1db939c)`. Nothing was missing from the data. The renderer
# threw it away.

# The reported case, as a fixture: a rung with no usable history (progress
# unknown), in a coarse phase, whose last line landed a couple of minutes ago.
STALLED_PUSH = {"rung": "auto", "phase": "push", "state": "running", "pid": 1,
                "elapsed": 256.0, "progress": None, "eta": None,
                "compile_lines": 0,
                "last": ">> reboot 1/4: burning a boot retry "
                        "(boot_id c1db939c)",
                # last_age is what the writer froze into the file; last_at is
                # when it happened. They disagree here ON PURPOSE -- see
                # test_the_quiet_time_keeps_climbing_after_the_writer_stops.
                "last_age": 0.1, "last_at": 1000.0, "started": 800.0}


def test_the_watch_block_shows_what_the_run_is_doing_not_a_phase_word():
    """The defect itself. `push` is a phase; `>> reboot 1/4: burning a boot
    retry` is what is HAPPENING, it was already in the status file, and the
    watcher never drew it."""
    lines = progress.watch_lines(STALLED_PUSH, width=100, now=1134.0)
    assert len(lines) == 3, lines
    assert all("\n" not in line for line in lines), lines
    assert "reboot 1/4" not in lines[-2], "the summary line never carried it"
    assert "reboot 1/4: burning a boot retry" in lines[-1], lines[-1]
    assert "2m14s" in lines[-1], lines[-1]


def test_the_quiet_time_keeps_climbing_after_the_writer_stops():
    """The difference between quiet and hung, and the reason the age is taken
    from `last_at` rather than from the `last_age` the writer stamped in.

    A build that wedges stops republishing, so `last_age` in the file is
    frozen at whatever it was -- 0.1s here -- and a watcher reading that field
    renders the same thing forever, which is precisely the "looks stale"
    complaint. Derived from the stamp, the number climbs on every repaint
    whatever the writer is doing.

    The clock is moved, the snapshot is NOT: same dict, two renders, and the
    age must differ by the minute that passed."""
    real_time = progress.time.time
    now = [1134.0]
    progress.time.time = lambda: now[0]
    try:
        first = progress.watch_lines(STALLED_PUSH, width=100)[-1]
        now[0] += 60.0
        second = progress.watch_lines(STALLED_PUSH, width=100)[-1]
    finally:
        progress.time.time = real_time
    assert "2m14s" in first, first
    assert "3m14s" in second, second


def test_a_run_that_has_said_nothing_yet_still_renders_every_row():
    """`publish_pending` stakes the status file with `last: ""` before the
    child speaks, and `watch` run straight after `--detach` reads exactly
    that. A block that loses a row there would smear the repaint."""
    pending = {"rung": "auto", "phase": "", "state": "running", "pid": 1,
               "elapsed": 0.0, "progress": None, "eta": None,
               "compile_lines": 0, "last": "", "last_at": 1000.0,
               "last_age": 0.0, "started": 1000.0}
    lines = progress.watch_lines(pending, width=100, now=1000.0)
    assert len(lines) == 3, lines
    assert all(line.strip() for line in lines), lines
    assert "auto" in lines[0], "the block must say WHAT is building"


def test_the_block_is_clipped_to_the_terminal_width():
    """`watch` repaints in place. A line wider than the terminal wraps, and
    the wrapped remainder is not walked back over on the next repaint -- it
    stays on screen as a smear that reads as a corrupted display."""
    long_line = dict(STALLED_PUSH, last=">> " + "verbose kbuild noise " * 12)
    lines = progress.watch_lines(long_line, width=60, now=1134.0)
    assert all(len(line) <= 60 for line in lines), [len(x) for x in lines]
    assert lines[-1].endswith("…"), lines[-1]
    assert "verbose kbuild noise" in lines[-1], lines[-1]


def test_an_unknown_bar_says_so_instead_of_shouting_question_marks():
    """`[??????????????????]` was the render for every rung without history --
    the common case -- and it reads as a broken terminal. What it must NOT
    become is something that implies a measurement nobody made: no fill, no
    percentage, and the same width so the columns after it do not move."""
    unknown, measured = progress.bar(None), progress.bar(0.5)
    assert "?" not in unknown, unknown
    assert "unknown" in unknown, unknown
    assert "=" not in unknown and ">" not in unknown, unknown
    assert len(unknown) == len(measured), (unknown, measured)


def test_the_stall_note_sits_beside_the_activity_not_instead_of_it():
    """The previous implementer chose the note INSTEAD of the raw line. The
    note explains a silence; only the line says what the silence is in the
    middle of. Both, on their own rows."""
    packaging = dict(STALLED_PUSH, phase="package",
                     last="[14:49:08] DONE!", last_at=1000.0)
    lines = progress.watch_lines(packaging, width=140, now=2200.0)
    assert "compress" in lines[-2] or "packag" in lines[-2].lower(), lines[-2]
    assert "DONE!" in lines[-1], lines[-1]
    assert "20m00s" in lines[-1], lines[-1]


def _running(rundir, **over):
    """The reported case as a live status file, timestamped now."""
    now = time.time()
    snap = dict(STALLED_PUSH, pid=os.getpid(), last_at=now - 134.0,
                started=now - 256.0, **over)
    path = pathlib.Path(rundir) / "x-status.json"
    path.write_text(json.dumps(snap))
    return path, snap


def test_watch_walks_back_over_every_row_it_painted_on_a_tty():
    """The bug this repo has shipped before: a tty-only render defect that no
    test could see, because tests have no tty. So drive `watch` with
    `tty=True` and assert the escape sequences.

    The invariant is arithmetic, not a literal: every newline written during
    an in-place repaint must be walked back up before the next one, or the
    rows above the last are frozen on screen -- which is exactly what "looks
    stale" looks like. Painting the second row with a bare `\r`, or clearing
    only the bottom row on the way out, both break it."""
    with tempfile.TemporaryDirectory() as rundir:
        path, snap = _running(rundir)
        captured = []

        def sink(text):
            captured.append(text)
            if sum("\033[2K" in t for t in captured) == 2:
                # Let it repaint a few times, then stop the loop.
                path.write_text(json.dumps(dict(snap, state="done")))

        rc = progress.watch(rundir, "x-status.json", 0.01, sink, tty=True)
    assert rc == progress.EX_OK
    block = "".join(t for t in captured if "\033[2K" in t)
    assert "reboot 1/4" in block, block
    assert block.count("\n") >= 2, "expected more than one repaint"
    walked = sum(int(n) for n in re.findall(r"\033\[(\d+)A", block))
    assert walked == block.count("\n"), (walked, block.count("\n"), repr(block))


def test_the_non_tty_watch_does_not_flood_the_log_with_the_second_row():
    """Off a tty this lands in a detached build's spawn log, which a periodic
    bar line had already made close to pure noise. The block is throttled to
    one every fifteen seconds however fast the loop spins -- so ten renders
    must still produce ONE block. The count is 2 because the closing
    `status_report` prints the same line once more, and that one is not part
    of the loop."""
    real_time, real_sleep = progress.time.time, progress.time.sleep
    clock, spins = [10_000.0], [0]
    with tempfile.TemporaryDirectory() as rundir:
        path, snap = _running(rundir)
        snap = dict(snap, last_at=clock[0] - 134.0, started=clock[0] - 256.0)
        path.write_text(json.dumps(snap))
        captured = []

        def fake_sleep(seconds):
            clock[0] += seconds
            spins[0] += 1
            if spins[0] >= 10:
                path.write_text(json.dumps(dict(snap, state="done")))

        progress.time.time = lambda: clock[0]
        progress.time.sleep = fake_sleep
        try:
            rc = progress.watch(rundir, "x-status.json", 1.0, captured.append,
                                tty=False)
        finally:
            progress.time.time, progress.time.sleep = real_time, real_sleep
    assert rc == progress.EX_OK
    assert spins[0] == 10, ("the loop must really have spun ten times, or "
                            "this proves nothing", spins[0])
    text = "".join(captured)
    assert "\033" not in text, "no escape sequences off a tty"
    assert text.count("reboot 1/4") == 2, text


# ------------------------------ the bar when the baseline is exhausted --
#
# fraction() returns None once a run overruns the history it was measured
# against, and it is right to: clamping to 0.99 there reported "99%, eta 7s"
# for the fourteen remaining minutes of a 14m42s build. But the fallback was
# NO ANSWER AT ALL for the rest of the run, which on a full compile is most of
# it. Three screenshots from one session -- at 1m42s, 4m38s and 6m09s -- all
# showed `[ unknown ] -- <phase>  eta --`. One defect, seen three times.

def _snap(**kw):
    base = {"rung": "fast", "phase": "package", "state": "running",
            "progress": None, "elapsed": 278.0, "eta": None,
            "last": "  CC arch/arm64/kernel/signal32.o", "last_at": NOW}
    base.update(kw)
    return base


NOW = 1_000_000.0


def test_no_fraction_falls_back_to_a_phase_position():
    line = progress.line_of(_snap(), now=NOW)
    assert "2/5" in line, line
    assert "unknown" not in line, line
    # A position, never dressed up as a percentage.
    assert "40%" not in line, line


def test_the_position_advances_with_the_phase():
    seen = [progress.line_of(_snap(phase=p), now=NOW)
            for p in ("make", "package", "install", "export", "flash")]
    assert ["1/5", "2/5", "3/5", "4/5", "5/5"] == \
        [l.split("]")[1].split()[0] for l in seen], seen


def test_a_real_fraction_still_wins():
    """THE POSITIVE CONTROL. A fallback that replaced the measurement would
    pass every assertion above while throwing away the only real number."""
    line = progress.line_of(_snap(progress=0.62, phase="make"), now=NOW)
    assert "62%" in line and "1/5" not in line, line


def test_a_rung_with_no_phase_table_says_unknown_rather_than_guessing():
    line = progress.line_of(_snap(rung="tkflash", phase="flash"), now=NOW)
    assert "unknown" in line, line
    line = progress.line_of(_snap(phase="nonsense"), now=NOW)
    assert "unknown" in line, line


def test_a_finished_run_is_not_still_in_its_last_phase():
    """`fast done [ unknown ] -- install  6m09s eta --` sat on screen after
    the phone had been flashed AND rebooted, and an agent went on polling a
    build that had been over for minutes."""
    line = progress.line_of(_snap(state="done", phase="install"), now=NOW)
    assert "done" in line and "install" not in line, line
    assert "100%" in line, line
    line = progress.line_of(_snap(state="failed", phase="install"), now=NOW)
    assert "failed" in line and "install" not in line, line


def test_a_stopped_run_cannot_be_stalled():
    line = progress.line_of(_snap(state="done", last_at=NOW - 9999), now=NOW)
    assert "no output for" not in line, line


def test_packaging_is_allowed_to_be_quiet():
    """pmbootstrap relays to log.txt, and packaging a kernel -- strip, then
    compress every module -- is silent for minutes on purpose. A flat 90s
    bound warned about a build that was working, which teaches the reader to
    distrust the one line that stops them killing a healthy one."""
    assert progress.stall_note("  CC foo.o", 120.0, "package") == ""
    # ...and the compile phase is still held to the tight bound.
    assert progress.stall_note("  CC foo.o", 120.0, "make") != ""
    # The positive control: packaging that is REALLY stuck still reports.
    assert progress.stall_note("  CC foo.o", 900.0, "package") != ""


def test_a_note_too_cut_to_read_is_dropped_rather_than_stubbed():
    """`-- the...` stops before the only clause carrying the reassurance."""
    tight = progress.line_of(_snap(phase="make", last_at=NOW - 400),
                             now=NOW, budget=85)
    assert "no output" not in tight, tight
    wide = progress.line_of(_snap(phase="make", last_at=NOW - 400),
                            now=NOW, budget=140)
    assert "no output for" in wide and "still running" in wide, wide


def main():
    return _runner.run(globals())


def test_watch_says_when_a_build_outlived_the_run_that_was_tracking_it():
    """Measured, on a real webkit build: `porthole aports build webkit2gtk-6.0`
    published .run/pkg-status.json, its own process was then killed, and the
    build kept going inside the workspace -- `podman exec`'s server side
    outlives its client. The file froze at 83.9% and stayed `running` with a
    dead pid. "webkit2gtk-6.0 finished 47m ago" was wrong; so is "it never
    came through porthole", because it did. The rung is what tells them
    apart, and the reader has to be told the lock died with the holder."""
    with tempfile.TemporaryDirectory() as rundir:
        frozen = time.time() - 47 * 60
        snap = {"rung": "pkg:webkit2gtk-6.0", "phase": "build",
                "state": "running", "pid": 999999999, "elapsed": 6832.5,
                "progress": 0.839, "eta": 17012.0, "compile_lines": 7907,
                "last": "[7907/9429] Building CXX", "last_at": frozen,
                "last_age": 41.0, "started": frozen - 6832.5}
        (pathlib.Path(rundir) / "x-status.json").write_text(json.dumps(snap))
        try:
            progress.watch(rundir, "x-status.json", 0.01, [].append,
                           tty=False, probe=lambda: "webkit2gtk-6.0")
            raise AssertionError("expected Bail: the tracker is gone")
        except progress.Bail as exc:
            assert "still building" in exc.message, exc.message
            assert "never" not in exc.message, "it DID come through porthole"
            assert "lock" in exc.hint, "the released flock is the danger"


def test_a_different_package_building_is_not_this_run_orphaned():
    """`orphaned` is what keeps the two sentences apart, so it must not call
    somebody else's build the resurrection of this one."""
    snap = {"rung": "pkg:phoc"}
    assert progress.orphaned(snap, "phoc")
    assert not progress.orphaned(snap, "webkit2gtk-6.0")
    assert not progress.orphaned(None, "phoc")
    assert not progress.orphaned(snap, "")


def test_watch_names_a_build_that_never_published_instead_of_the_last_run():
    """The sandbox is compiling right now, started outside porthole -- through
    `sandbox shell --command`, which takes no lock and writes no status file.
    The newest snapshot is then somebody else's finished run, and reporting it
    ("webgtk finished 24m ago", exit 0) is true about the file and wrong about
    the machine. The probe is what turns that into an answer."""
    with tempfile.TemporaryDirectory() as rundir:
        finished = time.time() - 24 * 60
        snap = {"rung": "pkg:phoc", "phase": "build", "state": "done",
                "pid": 1, "elapsed": 5.0, "progress": 1.0, "eta": 0.0,
                "compile_lines": 42, "last": "DONE!", "last_at": finished,
                "last_age": 0.0, "started": finished - 5.0}
        (pathlib.Path(rundir) / "x-status.json").write_text(json.dumps(snap))
        try:
            progress.watch(rundir, "x-status.json", 0.01, [].append,
                           tty=False, probe=lambda: "webgtk")
            raise AssertionError("expected Bail: webgtk is building")
        except progress.Bail as exc:
            assert "webgtk" in exc.message, exc.message
            assert "never published" in exc.message, exc.message
            assert exc.code != progress.EX_OK


def test_watch_does_not_probe_while_a_run_of_its_own_is_live():
    """The probe is a `podman exec` -- asked only when there is nothing here
    to follow, never once per poll of a healthy build."""
    with tempfile.TemporaryDirectory() as rundir:
        now = time.time()
        snap = {"rung": "pkg:phoc", "phase": "build", "state": "done",
                "pid": os.getpid(), "elapsed": 5.0, "progress": 1.0,
                "eta": 0.0, "compile_lines": 42, "last": "DONE!",
                "last_at": now, "last_age": 0.0, "started": now}
        (pathlib.Path(rundir) / "x-status.json").write_text(json.dumps(snap))
        asked = []

        def probe():
            asked.append(1)
            return "webgtk"

        rc = progress.watch(rundir, "x-status.json", 0.01, [].append,
                            tty=False, probe=probe)
        assert rc == progress.EX_OK
        assert not asked, "probed a run that was speaking for itself"


def test_a_snapshot_is_rebuilt_from_the_log_when_no_tracker_is_left():
    """The whole point: the tracker is gone and the build is 87% done. The
    log says so, and it was on the host's disk the entire time. These lines
    are the shape of the real webkit2gtk log this was written against --
    note there is no clock in them, which is why the rate is sampled."""
    text = "\n".join([
        "[8100/9429] Building CXX object Source/WebCore/a.cpp.o",
        "[8206/9429] Building CXX object Source/WebCore/c.cpp.o",
    ])
    snap = progress.snapshot_from_log(text, "webkit2gtk-6.0", mtime=1000.0,
                                      now=1041.0)
    assert snap["rung"] == "pkg:webkit2gtk-6.0"
    assert snap["steps"] == "8206/9429"
    assert 0.87 < snap["progress"] < 0.88, snap["progress"]
    assert snap["last_age"] == 41.0
    # Nothing here knows when the build began, and a number would be a lie.
    assert snap["elapsed"] is None and snap["started"] is None
    assert snap["pid"] is None
    # One reading has no speed.
    assert snap["rate"] is None and snap["eta"] is None
    # The renderers must survive every one of those Nones.
    assert "87%" in progress.line_of(snap), progress.line_of(snap)
    for line in progress.watch_lines(snap, width=100):
        assert line.strip()


def test_the_rate_is_the_tracker_s_own_window_not_a_second_one():
    """One way of getting a rate, whoever is counting. `window_rate` already
    refuses an answer when the span is too short or the steps too few, which
    is what stopped a build paused inside one emulated generator step from
    reporting 74 hours -- a reattached watcher inherits that refusal instead
    of reinventing it."""
    text = "[8206/9429] Building CXX object c.cpp.o"
    samples = [(1000.0, 8129), (1120.0, 8206)]
    snap = progress.snapshot_from_log(text, "webkit2gtk-6.0", mtime=1120.0,
                                      now=1120.0, samples=samples)
    assert abs(snap["rate"] - 77 / 120.0) < 0.001, snap["rate"]
    assert 1850 < snap["eta"] < 1950, snap["eta"]
    # Too few samples, too short a span: no rate, and so no ETA.
    thin = progress.snapshot_from_log(text, "webkit2gtk-6.0", mtime=1000.0,
                                      now=1000.0, samples=[(1000.0, 8206)])
    assert thin["rate"] is None and thin["eta"] is None


def test_a_log_with_no_steps_yet_still_yields_a_usable_snapshot():
    """Early in a build -- dependencies, fetching -- there is no `[n/N]` at
    all. A watcher must still paint something rather than crash."""
    text = "[23:19:31] (native) install alpine-sdk\n[23:19:40] >>> fetching"
    snap = progress.snapshot_from_log(text, "phoc", mtime=5.0, now=6.0)
    assert snap["progress"] is None and snap["steps"] is None
    assert snap["last"].startswith(">>>")
    assert progress.line_of(snap)


def test_reattach_follows_a_log_and_stops_when_the_build_leaves_the_workspace():
    """The loop itself, with a fake workspace: the log advances, the watcher
    paints what it says, and the ONLY thing that ends it is the process list
    -- a quiet log is not an ending, because packaging is silent for
    minutes."""
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log.txt"
        log.write_text("[8100/9429] Building CXX object a.cpp.o\n")
        alive = [True]
        ticks = []

        def probe():
            ticks.append(1)
            return "webkit2gtk-6.0" if alive[0] else ""

        def clock():
            # Far enough past the log's mtime that the probe is due every
            # time round, so the test does not sit through PROBE_AFTER_S.
            return time.time() + 3600

        lines = []

        def out(line):
            lines.append(line)
            if len(lines) == 2:  # the build ends while we are watching
                log.write_text("[9429/9429] Linking CXX shared library\n")
                alive[0] = False

        snap = progress.reattach(log, "webkit2gtk-6.0", probe, 0.01,
                                 out, tty=False, banner="  reattached",
                                 now=clock)
    assert ticks, "must have asked the workspace whether it was still there"
    assert snap["steps"] == "9429/9429", snap
    assert any("reattached" in line for line in lines)
    assert any("9429" in line for line in lines), lines


def test_a_quiet_log_is_not_treated_as_an_ending():
    """PROBE_AFTER_S exists so the free signal (the build ADVANCING) carries
    the common case and the podman exec is spent only on the ambiguous one."""
    assert progress.PROBE_AFTER_S >= 15


# Verbatim from ~/.local/var/porthole-sandbox/log.txt, 2026-09-01, the 5.5 h
# webkit2gtk-6.0 build that died on its last step. The three lines below the
# counter are the ones nothing was reading.
FAILED_TAIL = """\
[9427/9429] Linking CXX executable bin/MiniBrowser
[9428/9429] Generating WebKitWebProcessExtension-6.0.typelib
ninja: subcommand failed
>>> ERROR: webkit2gtk-6.0: build failed
(246720) [23:59:46] ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(246720) [23:59:46] ERROR: Couldn't build aarch64/webkit2gtk-6.0-2.52.6-r50.apk!
"""

# ...and what the next morning's unrelated `pmbootstrap chroot -- ls` appended
# to the very same file, eight hours later. This is the whole bug: it moves
# the mtime and says nothing whatever about webkit.
NEIGHBOUR = """\
(328393) [06:42:32] pmbootstrap v3.11.1
(328393) [06:42:37] NOTE: chroot is still active
(328393) [06:42:37] DONE!
"""


def test_the_log_says_how_the_build_ended_and_nothing_was_reading_it():
    """The reported defect, in one assert. A build that FAILED read as
    `running` forever because the only question anyone asked of the log was
    how recently it had been touched."""
    state, when, said = progress.log_outcome(
        FAILED_TAIL, "webkit2gtk-6.0", now=_at("2026-09-02 08:44:00"))
    assert state == "failed", state
    # The last thing THIS BUILD said -- not the last line in a file it shares
    # with every other build in the workspace.
    assert said == ">>> ERROR: webkit2gtk-6.0: build failed", said
    # Its own clock, from pmbootstrap's stamp -- not the file's mtime, which
    # by then belonged to something else entirely.
    assert when == _at("2026-09-01 23:59:46"), when


def test_a_neighbours_ending_is_not_this_builds():
    """log.txt is the WORKSPACE's, so it holds the endings of every build
    before this one. Only what was written after the newest `[n/N]` can be
    attributed, and only if it names this package."""
    assert progress.log_outcome(FAILED_TAIL, "phoc")[0] is None
    # The marker is real, but it is above the counter: a previous build's.
    stale = (">>> ERROR: webkit2gtk-6.0: build failed\n"
             "[100/9429] Building CXX object a.cpp.o\n")
    assert progress.log_outcome(stale, "webkit2gtk-6.0")[0] is None
    # abuild's other verdict, for the same reasons.
    built = ("[9429/9429] Linking CXX shared library lib/libwebkit2gtk.so\n"
             ">>> webkit2gtk-6.0*: Create webkit2gtk-6.0-2.52.6-r50.apk\n")
    assert progress.log_outcome(built, "webkit2gtk-6.0")[0] == "done"


def test_an_ending_outranks_the_mtime_of_a_log_every_build_shares():
    """A fresh mtime is not evidence this build lives, and a stale one is not
    evidence it died -- both are facts about a file the whole workspace
    writes to. `>>> ERROR: ...: build failed` is evidence, at any age."""
    frozen = {"rung": "pkg:webkit2gtk-6.0", "state": "running",
              "pid": 999999999, "elapsed": 6832.0, "started": 1.0}
    now = _at("2026-09-02 08:44:00")
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log.txt"
        # Touched ten seconds ago by the neighbour, so every old rule called
        # this a live build and drew it at 99%.
        log.write_text(FAILED_TAIL + NEIGHBOUR)
        os.utime(log, (now - 10, now - 10))
        snap = progress.reattach_from_log(log, frozen, now=now)
        assert snap["state"] == "failed", snap
        assert snap["last_at"] == _at("2026-09-01 23:59:46"), snap["last_at"]
        assert snap["eta"] is None
        assert progress.liveness(snap) == "failed"

        # ...and the same log gone quiet still reports the ending, where
        # before the staleness rule threw the answer away with the file.
        os.utime(log, (now - 9000, now - 9000))
        assert progress.reattach_from_log(log, frozen,
                                          now=now)["state"] == "failed"


def test_a_watch_reaches_its_verdict_without_waiting_for_the_workspace():
    """`porthole pkg watch` reported that the build was done and never once
    said whether it worked. The probe that ends a reattached watch was gated
    behind the LOG going quiet, and on a workspace anybody is using it never
    does -- so the loop sat on a finished build until somebody killed it."""
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log.txt"
        log.write_text("[9427/9429] Linking CXX executable bin/MiniBrowser\n")
        asked = []
        ticks = [0]

        def probe():
            asked.append(True)
            return "webkit2gtk-6.0"          # the workspace still says yes

        def clock():
            ticks[0] += 1
            if ticks[0] == 3:
                log.write_text(FAILED_TAIL)
            return 1000.0 + ticks[0]

        snap = progress.reattach(log, "webkit2gtk-6.0", probe, 0.001,
                                 lambda _line: None, tty=False, now=clock)
    assert snap["state"] == "failed", snap
    assert not asked, "the log already answered; nothing to ask podman"


UTF = progress.Style(colour=False, unicode=True)
FANCY = progress.Style(colour=True, unicode=True)


def test_the_chrome_sits_at_the_bottom_dim_and_never_wraps():
    """Both halves used to print at the TOP in full brightness, before the
    bar -- so the eye met two lines of chrome first, and on a narrow terminal
    the longer one wrapped INTO the bar. They are the least important text on
    screen: last, grey, one line, clipped rather than wrapped."""
    foot = progress.footer_of("reattached: tracker gone", tty=True)
    assert "Ctrl-C" in foot and "keeps running" in foot
    assert "reattached" in foot
    assert "\n" not in foot
    # No terminal: no keyboard to tell about, and an agent gets no prose.
    assert progress.footer_of("reattached: tracker gone", tty=False) == ""
    # Narrow: the reassuring clause goes first, then the hint entirely. The
    # reason survives longest -- it is the half a reader cannot reconstruct.
    wide = progress.footer_of("reattached: tracker gone", True, 200)
    snug = progress.footer_of("reattached: tracker gone", True, 52)
    tiny = progress.footer_of("reattached: tracker gone", True, 20)
    assert "keeps running" in wide
    assert "Ctrl-C" in snug and "keeps running" not in snug
    assert tiny == "reattached: tracker gone"

    snap = dict(STALLED_PUSH)
    rows = progress.watch_lines(snap, width=60, now=1134.0, style=FANCY,
                                footer=foot)
    assert len(rows) == 4, rows
    assert all(progress.visible_len(row) <= 60 for row in rows)
    # The footer is the LAST row, and the only colour on it is grey.
    assert "Ctrl-C" in rows[-1]
    assert rows[-1].startswith("\033[90m"), rows[-1]
    # ...and the block keeps its shape when there is nothing to say.
    assert len(progress.watch_lines(snap, width=60, now=1134.0)) == 3


def test_the_eye_lands_on_the_percentage_and_the_eta():
    """Emphasis is a budget. The percentage, the ETA and the name are what
    somebody opens this to read; the rate, the elapsed and every label are
    context and are dimmed."""
    snap = {"rung": "pkg:webkit2gtk-6.0", "phase": "build",
            "state": "running", "pid": None, "elapsed": None,
            "progress": 0.87, "eta": 1800.0, "compile_lines": 8206,
            "last": "[8206/9429] Building CXX", "last_at": 1000.0,
            "last_age": 1.0, "started": None, "rate": 0.1,
            "step": "compile", "steps": "8206/9429"}
    rows = progress.watch_lines(snap, width=96, now=1001.0, style=FANCY)
    bold, grey = "\033[1m", "\033[90m"
    assert bold + "webkit2gtk-6.0" in rows[0], rows[0]
    assert grey + "pkg:" in rows[0], "the namespace is context, not the name"
    assert bold + "  87%" in rows[1], rows[1]
    assert bold + " 30m00s" in rows[1], "the ETA is what they are waiting for"
    assert grey in rows[1], "the labels and the rate must be dimmed"
    # Colour must not change how wide any of it is.
    plain = progress.watch_lines(snap, width=96, now=1001.0, style=UTF)
    assert [progress.visible_len(r) for r in rows] == [len(r) for r in plain]


def test_the_header_is_built_from_segments_not_reparsed_text():
    """The painter used to look for the state at the end of the line it had
    just built, and for a `/` in the last word. The moment the step count
    arrived it printed `webkit2gtk-6.0    8358358/9429running` -- a renderer
    that reverse-engineers its own output is a bug waiting for its next
    field."""
    snap = {"rung": "pkg:webkit2gtk-6.0", "state": "running",
            "steps": "8358/9429"}
    plain = progress.header_of(snap, 110)
    assert plain.count("8358") == 1, plain
    assert "8358/9429   running" in plain, plain
    assert len(plain) == 110 - 2 or len(plain) <= 110, plain
    painted = progress._paint_header(snap, 110, "cyan", FANCY)
    assert progress.visible_len(painted) == len(plain)
    assert painted.count("8358") == 1, painted

    # As the terminal narrows: steps go first, then the state, never the name.
    assert "8358/9429" in progress.header_of(snap, 60)
    assert "8358/9429" not in progress.header_of(snap, 36)
    assert "running" in progress.header_of(snap, 36)
    tiny = progress.header_of(snap, 20)
    assert "webkit" in tiny and len(tiny) <= 20, tiny


def test_the_line_the_build_printed_is_not_dimmed_like_the_chrome():
    """The activity text and the footer were rendering in the same grey, so
    the two most different things on screen -- what the build is doing, and
    how to quit -- looked identical."""
    snap = dict(STALLED_PUSH, steps="8/9")
    rows = progress.watch_lines(snap, width=100, now=1134.0, style=FANCY,
                                footer="Ctrl-C stops watching")
    activity, footer = rows[2], rows[3]
    assert footer.startswith("\033[90m"), footer
    # The age is dim; the build's own words are not.
    assert "\033[90m" in activity, activity
    assert activity.rstrip().endswith("\033[0m") is False or \
        "reboot" in progress._ANSI.sub("", activity)
    plain_text = progress._ANSI.sub("", activity)
    said = plain_text.split("ago", 1)[1]
    assert said.strip(), said
    assert "\033[90m" + said not in activity, "the build's line must not be grey"


def test_one_rule_decides_whether_a_build_outlived_its_tracker():
    """`watch`, `status` and the status line all have to ask it, and three
    copies would be three chances to disagree about whether a build is
    alive. Every one of the four conditions is load-bearing."""
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "log.txt"
        log.write_text("[8591/9429] Building CXX object a.cpp.o\n")
        now = time.time()
        os.utime(log, (now - 3, now - 3))
        frozen = {"rung": "pkg:webkit2gtk-6.0", "state": "running",
                  "pid": 999999999, "elapsed": 6832.0, "progress": 0.839,
                  "last_at": now - 2900, "started": now - 9700}

        got = progress.reattach_from_log(log, frozen, now=now)
        assert got and got["steps"] == "8591/9429", got
        assert got["rung"] == "pkg:webkit2gtk-6.0"

        # A run that ENDED properly is not an orphan.
        assert progress.reattach_from_log(
            log, dict(frozen, state="done"), now=now) is None
        # A tracker that is alive describes its own build better than we can.
        assert progress.reattach_from_log(
            log, dict(frozen, pid=os.getpid()), now=now) is None
        # A quiet log is not a running build...
        assert progress.reattach_from_log(
            log, frozen, now=now + progress.LOG_FRESH_S + 60) is None
        # ...and neither is one dated in the future: that is a clock that
        # disagrees, not evidence of work.
        os.utime(log, (now + 3600, now + 3600))
        assert progress.reattach_from_log(log, frozen, now=now) is None
        # No log at all is simply no answer.
        assert progress.reattach_from_log(
            pathlib.Path(tmp) / "nope.txt", frozen, now=now) is None


def test_a_finished_invocation_is_recognised_whatever_trails_it():
    """pmbootstrap writes DONE! at the end of every invocation that finishes,
    and a chroot trailer after it. A running build has not printed it -- which
    is why this is the test and "are there compile lines in the tail" is not:
    a real build is silent for minutes during packaging."""
    ended = ("(1) [18:45] (native) % su pmos -c 'ccache -s'\n"
             "(1) [18:45] NOTE: chroot is still active (use 'pmbootstrap "
             "shutdown' as necessary)\n"
             "(1) [18:45] DONE!\n\n")
    assert progress.log_invocation_ended(ended)
    assert progress.log_invocation_ended("(1) [18:45] DONE!")
    building = ("(1) [18:45] DONE!\n"
                "(1) [18:46]   CC      drivers/gpu/msm.o\n")
    assert not progress.log_invocation_ended(building), (
        "an earlier DONE! must not outrank the compile lines after it")
    assert not progress.log_invocation_ended("")
    assert not progress.log_invocation_ended("  AR built-in.a\n")


def test_prose_about_a_step_does_not_become_the_step():
    """`porthole build status` reported `phase flash` about a finished `image`
    run, which never flashes anything. Every `>>` line was read as an
    announcement, so detail lines and quoted commands set the phase:

        >>   this rung flashes the aport apk, so the tree cannot affect it.
        >>   and `pmbootstrap export` packs boot.img from it.
        NOTE: To export the rootfs image, run 'pmbootstrap install' first

    ph-build.sh's own convention answers it: `>> ` announces a step, `>>   `
    explains one, and 63 of its detail lines already follow that.
    """
    keeps = [
        ">>   this rung flashes the aport apk, so the tree cannot affect it.",
        ">>   device. boot.img above is complete and flashable:",
        ">>     porthole run tools/tk-flash-boot.sh",
        ">> NOTE: no rootfs image was made -- this workspace has no loop",
        ">> WARNING: no .device-uuids -- flashing the export UUIDs unchecked",
        ">>   and `pmbootstrap export` packs boot.img from it.",
        "[18:55] NOTE: To export the rootfs image, run 'pmbootstrap install' first",
        ">>   pmbootstrap installed here, with its chroots):",
    ]
    for line in keeps:
        assert progress.phase_of(line, "package") == "package", line

    # THE POSITIVE CONTROL: the real announcements must still be read, or this
    # is indistinguishable from turning phase detection off.
    for line, want in (
            (">> installing the system into the rootfs chroot", "install"),
            (">> exporting the built image (pmbootstrap export)", "export"),
            (">> verifying the exported image", "verify"),
            (">> flashing boot", "flash"),
            (">> pushing the module", "push")):
        assert progress.phase_of(line, "") == want, (line, want)



def test_a_chroot_session_is_not_a_build():
    """The log is shared by the whole workspace. `pmbootstrap status`,
    `pmbootstrap log`, a `chroot -- ccache -s` and an agent sitting in an open
    `pmbootstrap chroot` all write it, and none of them is a build. Its mtime
    says only that some pmbootstrap ran."""
    assert not progress.names_a_build(
        "(1234) [01:00:00] % sh -c echo hi\n"
        "(1234) [01:00:01] (native) % busybox su pmos -c HOME=/home/pmos sh ;\n")
    assert not progress.names_a_build("")
    assert not progress.names_a_build(
        "(1234) [01:00:01] (native) % su pmos -c 'ccache -s'\n")


def test_the_lines_only_a_build_writes():
    """Three shapes, all taken from a real log.txt on the reference host:
    ninja's step counter, abuild's own banners, and the kernel's kbuild
    prefixes. One of them is in the tail throughout a build -- including the
    quiet packaging phase, where abuild is the only thing talking."""
    assert progress.names_a_build("[9429/9429] Generating WebKit-6.0.typelib\n")
    assert progress.names_a_build(
        ">>> webkit2gtk-6.0: Build complete at Sun, 06 Sep 2026 18:26:28 +0000\n")
    assert progress.names_a_build(">>> webkit2gtk-6.0: Entering fakeroot...\n")
    assert progress.names_a_build("  CC      drivers/media/i2c/imx179.o\n")


def test_one_build_line_anywhere_in_the_tail_is_enough():
    """A build is legitimately silent for minutes during packaging, and the
    64 KiB tail still holds what it said before it went quiet. Requiring the
    LAST line to be build-shaped would call a healthy build a phantom."""
    text = ("[9429/9429] Generating WebKit-6.0.typelib\n"
            + "(1234) [01:00:01] (native) % busybox su pmos -c sh ;\n" * 50)
    assert progress.names_a_build(text)

if __name__ == "__main__":
    sys.exit(main())
