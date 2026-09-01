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
sys.path.insert(0, str(ROOT / "lib"))

import porthole_progress as progress  # noqa: E402


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
    assert len(lines) == 2, lines
    assert all("\n" not in line for line in lines), lines
    assert "reboot 1/4" not in lines[0], "the summary line never carried it"
    assert "reboot 1/4: burning a boot retry" in lines[1], lines[1]
    assert "2m14s" in lines[1], lines[1]


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
        first = progress.watch_lines(STALLED_PUSH, width=100)[1]
        now[0] += 60.0
        second = progress.watch_lines(STALLED_PUSH, width=100)[1]
    finally:
        progress.time.time = real_time
    assert "2m14s" in first, first
    assert "3m14s" in second, second


def test_a_run_that_has_said_nothing_yet_still_renders_two_lines():
    """`publish_pending` stakes the status file with `last: ""` before the
    child speaks, and `watch` run straight after `--detach` reads exactly
    that. A block that loses a row there would smear the repaint."""
    pending = {"rung": "auto", "phase": "", "state": "running", "pid": 1,
               "elapsed": 0.0, "progress": None, "eta": None,
               "compile_lines": 0, "last": "", "last_at": 1000.0,
               "last_age": 0.0, "started": 1000.0}
    lines = progress.watch_lines(pending, width=100, now=1000.0)
    assert len(lines) == 2, lines
    assert lines[1].strip(), "the second row must never be blank"


def test_the_block_is_clipped_to_the_terminal_width():
    """`watch` repaints in place. A line wider than the terminal wraps, and
    the wrapped remainder is not walked back over on the next repaint -- it
    stays on screen as a smear that reads as a corrupted display."""
    long_line = dict(STALLED_PUSH, last=">> " + "verbose kbuild noise " * 12)
    lines = progress.watch_lines(long_line, width=60, now=1134.0)
    assert all(len(line) <= 60 for line in lines), [len(x) for x in lines]
    assert lines[1].endswith("…"), lines[1]
    assert "verbose kbuild noise" in lines[1], lines[1]


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
    assert "compress" in lines[0] or "packag" in lines[0].lower(), lines[0]
    assert "DONE!" in lines[1], lines[1]
    assert "20m00s" in lines[1], lines[1]


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
