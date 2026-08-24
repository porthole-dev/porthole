#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The screens, driven by Textual's pilot.

Skips loudly on the 3.8/3.11/3.13 matrix where textual is absent; the `console`
CI job installs it and fails if these skip there.
"""
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tui_harness  # noqa: E402

tui_harness.require()

ROOT = pathlib.Path(__file__).resolve().parent.parent

from textual.widgets import (Button, Input, ListView, RichLog,  # noqa: E402
                             Select, Static)

from porthole_tui.app import PortholeApp, SECTIONS  # noqa: E402
from porthole_tui.screens.confirm import ConfirmRun  # noqa: E402
from porthole_tui.screens.form import ArgForm, _picker_start  # noqa: E402
from porthole_tui.screens.help import HelpScreen  # noqa: E402
from porthole_tui.screens.picker import FilePicker  # noqa: E402
from porthole_tui.screens.reader import Reader  # noqa: E402
from porthole_tui.widgets.brain import NoteList  # noqa: E402
from porthole_tui.widgets.devices import DeviceList  # noqa: E402
from porthole_tui.widgets.jobs import JobDrawer  # noqa: E402
from porthole_tui.widgets.port import PortView  # noqa: E402
from porthole_tui.widgets.rail import Rail  # noqa: E402
from porthole_tui.widgets.tools import ToolList  # noqa: E402
from porthole_tui.widgets import palette as pal  # noqa: E402


def make():
    return PortholeApp(ROOT)


def test_it_starts_and_shows_a_rail():
    async def body(app, pilot):
        assert app.screen.query_one(Rail) is not None
    tui_harness.pilot(make, body)


def test_number_keys_switch_section():
    async def body(app, pilot):
        await pilot.press("3")
        await pilot.pause()
        assert app.screen.section == "tools", app.screen.section
        await pilot.press("1")
        await pilot.pause()
        assert app.screen.section == "port"
    tui_harness.pilot(make, body)


def test_tab_actually_moves_focus_between_regions():
    """Not just 'something is focused' -- that passes against a dead binding.

    Textual's Screen ships `tab -> app.focus_next`; the `app.` prefix routes the
    action to the App. Redeclaring it unqualified on a Screen REPLACES that
    default with one that resolves against the Screen, where no
    action_focus_next exists, and dispatch silently fails. This test exists
    because that happened (ruling R21).
    """
    async def body(app, pilot):
        await pilot.pause()
        seen = [id(app.focused)]
        for _ in range(4):
            await pilot.press("tab")
            await pilot.pause()
            seen.append(id(app.focused))
        assert app.focused is not None, "focus must never be nowhere"
        assert len(set(seen)) > 1, \
            "tab never moved focus: {}".format(
                [type(app.focused).__name__ for _ in seen])
    tui_harness.pilot(make, body)


def test_escape_does_not_quit():
    # Quitting on Esc while something is open loses the whole session to a
    # reflex. Esc closes the topmost thing; only q quits.
    async def body(app, pilot):
        await pilot.press("escape")
        await pilot.pause()
        assert app.is_running
    tui_harness.pilot(make, body)


def test_an_unimplemented_section_shows_a_placeholder_not_a_crash():
    # LOGS and JOBS land in later plans. Until then the screen must say so.
    async def body(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        assert app.screen.section == "logs"
        assert app.is_running
    tui_harness.pilot(make, body)


def test_every_section_has_a_key():
    assert len(SECTIONS) <= 9, "1-9 must be able to address them all"


def test_filtering_and_opening_takes_one_enter_not_two():
    # The old console delegated every key to the filter while it was open and
    # had no enter-to-open path, so narrowing a 98-item list and opening a
    # result took two identical-looking keystrokes and only the second
    # worked. This exercises the actual keystrokes -- focus the filter, type,
    # press enter -- and checks BOTH that focus lands on the list AND that
    # the query survives, which is the whole fix for the two-enters bug.
    async def body(app, pilot):
        await pilot.press("3")
        await pilot.pause()
        listing = app.screen.query_one(ToolList)
        await pilot.press("slash")
        await pilot.pause()
        assert listing.query_one("#filter", Input).has_focus, \
            "/ must focus the filter"
        for ch in "firstpaint":
            await pilot.press(ch)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert listing.query_one("#rows", ListView).has_focus, \
            "enter must move focus to the list"
        assert listing.query == "firstpaint", \
            "enter must not clear the filter it just narrowed: {!r}".format(
                listing.query)
    tui_harness.pilot(make, body)


def test_a_filter_that_matches_nothing_says_so():
    async def body(app, pilot):
        await pilot.press("3")
        await pilot.pause()
        listing = app.screen.query_one(ToolList)
        listing.query = "zzzznope"
        await pilot.pause()
        assert listing.visible_rows() == []
        assert listing.empty_message
    tui_harness.pilot(make, body)


def test_the_brain_filter_never_hides_the_laws():
    # Hiding the laws from someone who filtered to their SoC is exactly
    # backwards: the laws are the notes that stop you wasting a week. A fixed
    # snapshot with a KNOWN law note, not whatever the repo's brain/ happens
    # to hold right now -- so this cannot pass vacuously if the corpus ever
    # loses its laws.
    import pathlib as _pathlib

    from porthole_cmd_brain import Note as _Note
    from porthole_tui import state as _state

    def _note(id_, severity, title="something else entirely"):
        return _Note(_pathlib.Path("brain/x/{}.md".format(id_)),
                    {"id": id_, "title": title, "severity": severity}, "")

    snap = _state.Snapshot(
        device="", devices=[], cfg={}, rows=[], summary={}, tools=[],
        error=None, stamp=1.0,
        notes=[_note("law-one", "law", "always fsync before flashing"),
              _note("fact-one", "fact"), _note("fact-two", "fact")])

    async def body(app, pilot):
        # MainScreen._poll() overwrites every content widget's .snapshot with
        # the real store's on its first tick (rail.snapshot is None), which
        # races the fabricated snapshot below -- reproduced for real under
        # load, with the store's own (still-empty, mid-refresh) snapshot
        # clobbering this one and failing the assertion for a reason that
        # had nothing to do with the brain filter. Settling the real store
        # and priming that first tick before injecting the fixture, the same
        # way test_a_filtered_list_says_how_many_of_how_many and
        # test_every_device_row_... already do, makes later ticks no-ops.
        app.store.refresh(block=True)
        await pilot.press("4")
        await pilot.pause()
        app.screen._poll()
        notes = app.screen.query_one(NoteList)
        notes.snapshot = snap
        await pilot.pause()
        notes.query = "zzzznope"
        await pilot.pause()
        kept = {n.meta.get("severity") for _label, n in notes.visible_rows()}
        assert kept == {"law"}, kept
    tui_harness.pilot(make, body)


def test_every_device_row_shows_its_own_paths_not_the_active_one():
    """The pane exists to make a cross-device isolation mistake visible.

    The working repo and the pmaports checkout used to be global, so
    switching devices left you in the previous device's files. A row that
    shows a path only for the device you are already on cannot reveal that a
    DIFFERENT device points somewhere wrong (ruling R22).
    """
    async def body(app, pilot):
        app.store.refresh(block=True)
        await pilot.press("2")
        await pilot.pause()
        listing = app.screen.query_one(DeviceList)
        listing.snapshot = app.store.snapshot
        await pilot.pause()
        rows = listing.visible_rows()
        if len(rows) < 2:
            return                      # single-device checkout: nothing to compare
        labels = [label for label, _payload in rows]
        assert not any(label.rstrip().endswith("-") for label in labels), \
            "a non-active device is showing a dash instead of its own paths: {}".format(labels)
        # the whole point: two devices must be distinguishable by their paths
        assert len(set(labels)) == len(labels), labels
    tui_harness.pilot(make, body)


def test_a_pane_with_no_data_shows_its_empty_message_not_a_blank():
    async def body(app, pilot):
        await pilot.press("3")
        await pilot.pause()
        listing = app.screen.query_one(ToolList)
        listing.snapshot = None
        await pilot.pause()
        assert listing.visible_rows() == []
    tui_harness.pilot(make, body)


def test_a_filtered_list_says_how_many_of_how_many():
    # "am I seeing all of it" is the question a count answers and a
    # scrollbar does not -- the same reason the curses console rendered
    # "12-30 of 55" instead of a bar.
    async def body(app, pilot):
        await pilot.press("3")
        await pilot.pause()
        listing = app.screen.query_one(ToolList)
        # The store refreshes on a worker thread; block for a real snapshot
        # rather than racing the 0.4s poller with more pilot.pause() calls.
        app.store.refresh(block=True)
        listing.snapshot = app.store.snapshot
        await pilot.pause()
        total = len(listing.rows(listing.snapshot))
        listing.query = "firstpaint"
        await pilot.pause()
        shown = len(listing.visible_rows())
        assert 0 < shown < total, (shown, total)
        summary = listing.count_label()
        assert str(shown) in summary and str(total) in summary, summary
    tui_harness.pilot(make, body)


def test_a_dangerous_command_cannot_run_without_a_confirm():
    async def body(app, pilot):
        app.screen.launch("porthole flash boot --slot b", safe=True)
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRun), \
            "a flash marked safe in the table must still confirm"
        assert app.jobs.jobs == [], "nothing may spawn before the confirm"
    tui_harness.pilot(make, body)


def test_the_confirm_reproduces_the_exact_command():
    async def body(app, pilot):
        command = "porthole flash boot --slot b"
        app.screen.launch(command, safe=True)
        await pilot.pause()
        assert app.screen.command == command
    tui_harness.pilot(make, body)


def test_any_key_but_y_cancels():
    async def body(app, pilot):
        app.screen.launch("porthole flash boot", safe=True)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert app.jobs.jobs == [], "cancel must not spawn"
    tui_harness.pilot(make, body)


def test_enter_does_not_confirm():
    # No default button, no enter-to-accept: this is the one dialog where
    # muscle memory must not be able to answer.
    async def body(app, pilot):
        app.screen.launch("porthole flash boot", safe=True)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.jobs.jobs == []
    tui_harness.pilot(make, body)


def test_a_safe_read_only_command_runs_without_asking():
    async def body(app, pilot):
        app.screen.launch("porthole brief", safe=True)
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmRun)
        assert len(app.jobs.jobs) == 1
        app.jobs.cancel_all()
    tui_harness.pilot(make, body)


def test_a_description_is_refused_as_a_command():
    async def body(app, pilot):
        app.screen.launch("read the panel datasheet", safe=True)
        await pilot.pause()
        assert app.jobs.jobs == [], "only `porthole ...` may be spawned"
    tui_harness.pilot(make, body)


# -- Binding-resolution proofs -------------------------------------------
#
# R20: a Binding whose action is not defined on the Screen it is declared on
# silently does nothing. Every Binding added in this task resolves against
# its OWN class (Reader.action_close/action_run, HelpScreen.action_close), so
# there is no app.-prefix case to prove here -- but "defined on the right
# class" is exactly the kind of thing that looks right and is dead, so each
# one is driven with the pilot and its effect is asserted, not assumed.

def test_reader_x_actually_runs_the_bound_command():
    # Proves Reader's Binding("x", "run", ...) -> action_run really fires.
    async def body(app, pilot):
        from porthole_tui.content import Doc
        doc = Doc("a tool", ["some lines"], command="porthole brief",
                  safe=True)
        seen = []
        app.push_screen(Reader(doc), seen.append)
        await pilot.pause()
        assert isinstance(app.screen, Reader)
        await pilot.press("x")
        await pilot.pause()
        assert seen == ["porthole brief"], \
            "pressing x did not dismiss with the doc's command: {}".format(seen)
    tui_harness.pilot(make, body)


def test_reader_escape_and_q_close_without_running():
    # Proves Reader's Binding("escape,q", "close", ...) -> action_close.
    async def body(app, pilot):
        from porthole_tui.content import Doc
        for key in ("escape", "q"):
            doc = Doc("a tool", ["some lines"], command="porthole brief")
            seen = []
            app.push_screen(Reader(doc), seen.append)
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            assert seen == [None], \
                "{!r} must close the reader without running anything: {}".format(
                    key, seen)
    tui_harness.pilot(make, body)


def test_question_mark_opens_help_and_escape_closes_it():
    # Proves MainScreen's existing Binding("question_mark", "help", ...) now
    # reaches a real HelpScreen (Task 9 shipped only a stub), and that
    # HelpScreen's own Binding("escape,q,question_mark", "close", ...) ->
    # action_close fires and returns to MainScreen rather than quitting.
    async def body(app, pilot):
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
        assert app.is_running
    tui_harness.pilot(make, body)


def test_generated_help_lists_a_real_binding_it_did_not_hardcode():
    # If this drifted to a hand-written string, this test could not tell --
    # it asserts against MainScreen's OWN live BINDINGS, the same source the
    # screen reads, so it only passes if _sources()/_of() actually walked
    # the real bindings map rather than returning nothing.
    async def body(app, pilot):
        main_screen = app.screen
        expected = [b for _key, b in main_screen._bindings
                    if b.description]
        assert expected, "MainScreen has no described bindings to check against"
        sample = expected[0]

        await pilot.press("question_mark")
        await pilot.pause()
        blob = "\n".join(str(widget.render())
                         for widget in app.screen.query(Static))
        assert (sample.key_display or sample.key) in blob, blob
        assert sample.description in blob, blob
    tui_harness.pilot(make, body)


def test_selecting_a_tool_opens_the_reader_then_x_asks_to_confirm():
    # End-to-end: a real Chosen message drives on_catalogue_chosen, which
    # pushes a real Reader; x inside it dismisses with the tool's command,
    # whose safe=False routes it back through launch() into a real confirm.
    async def body(app, pilot):
        await pilot.press("3")
        await pilot.pause()
        listing = app.screen.query_one(ToolList)
        if not listing.visible_rows():
            return                      # no tools in this checkout
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Reader), type(app.screen)
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRun), \
            "a tool's run command must still be confirmed"
        await pilot.press("y")
        await pilot.pause()
        assert len(app.jobs.jobs) == 1
        app.jobs.cancel_all()
    tui_harness.pilot(make, body)


# -- Task 13: the job drawer ----------------------------------------------
#
# The drawer's own ctrl+j/ctrl+c/ctrl+r are declared as Bindings on
# MainScreen, not on JobDrawer itself, and MainScreen's actions delegate into
# the drawer. Measured with a probe before writing this: a Binding on a
# WIDGET only resolves while focus is inside that widget's own subtree --
# JobDrawer docks outside #content, so with focus on a catalogue's #rows (the
# normal state) a Binding declared on JobDrawer never appears in the
# resolution chain at all. That would have been a fifth R20 instance,
# undetectable by a test that calls action_toggle()/action_rerun() directly
# instead of pressing the key. Every test below drives the real key.

def test_output_reaches_the_drawer_while_the_job_runs():
    # drawer.text() reads job.lines directly, which the JobManager's pump
    # fills regardless of the drawer -- asserting on it alone cannot tell
    # whether _drain (the 0.1s timer that copies job.lines into the
    # RichLog) is actually running. It was measured to still pass with
    # _drain's copy loop stubbed out entirely, so the RichLog itself -- the
    # thing _drain actually writes -- is asserted on too. RichLog defers
    # rendering until its size is known, and #job-log is `display: none`
    # while collapsed, so the drawer is expanded first -- CSS makes it
    # visible, flushing whatever _drain queued.
    #
    # The job outlives the check by cancelling it, rather than a short
    # `sleep 0.5` raced against `pilot.pause(0.25)`: under real CPU
    # contention (measured with 8 background `yes` processes on this box)
    # the 0.5s background sleep sometimes finished before the pause
    # returned, so "it must still be running" failed for a reason that had
    # nothing to do with the drawer.
    async def body(app, pilot):
        drawer = app.screen.query_one(JobDrawer)
        job = app.jobs.spawn("sh -c 'echo hello; sleep 100'")
        drawer.attach(job)
        await pilot.pause(0.25)
        assert "hello" in drawer.text(), drawer.text()
        drawer.expanded = True
        await pilot.pause()
        log = drawer.query_one("#job-log", RichLog)
        rendered = "\n".join(strip.text for strip in log.lines)
        assert "hello" in rendered, \
            "the drawer's RichLog never received the job's output: {!r}".format(
                rendered)
        assert job.state == "running", "it must still be running"
        job.cancel()
        await job.wait()
    tui_harness.pilot(make, body)


def test_the_drawer_expands_and_collapses():
    # Proves MainScreen's Binding("ctrl+j", "toggle_drawer") ->
    # MainScreen.action_toggle_drawer -> JobDrawer.action_toggle really fires,
    # with focus sitting on the default-focused catalogue, not the drawer.
    async def body(app, pilot):
        drawer = app.screen.query_one(JobDrawer)
        assert not drawer.expanded
        assert not isinstance(app.focused, JobDrawer)
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert drawer.expanded
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert not drawer.expanded
    tui_harness.pilot(make, body)


def test_ctrl_c_actually_cancels_the_running_job():
    # Proves MainScreen's Binding("ctrl+c", "cancel_job") ->
    # MainScreen.action_cancel_job -> JobDrawer.action_cancel really fires.
    # ctrl+c is otherwise claimed by Textual itself (App: help_quit,
    # Screen: copy_text) -- MainScreen's own binding must win, and must not
    # quit the app or merely notify.
    async def body(app, pilot):
        drawer = app.screen.query_one(JobDrawer)
        job = app.jobs.spawn("sh -c 'sleep 5'")
        drawer.attach(job)
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause(0.1)
        assert job._cancelled_at is not None, \
            "ctrl+c did not reach action_cancel"
        assert app.is_running, "ctrl+c must cancel the job, not quit the app"
        await job.wait()
        assert job.state == "cancelled", job.state
    tui_harness.pilot(make, body)


def test_rerunning_from_history_confirms_again():
    # A new risk the job runner creates: a dangerous command must never be
    # one keypress from a drawer. Having run once proves it was confirmed
    # once, and confirming once must not buy a second run.
    async def body(app, pilot):
        drawer = app.screen.query_one(JobDrawer)
        job = app.jobs.spawn("sh -c 'true'")
        await job.wait()
        job.command = "porthole flash boot"
        drawer.attach(job)
        drawer.action_rerun()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRun)
    tui_harness.pilot(make, body)


def test_ctrl_r_reruns_through_the_confirm_boundary():
    # Proves MainScreen's Binding("ctrl+r", "rerun_job") ->
    # MainScreen.action_rerun_job -> JobDrawer.action_rerun really fires, via
    # the actual key rather than calling the method directly.
    async def body(app, pilot):
        drawer = app.screen.query_one(JobDrawer)
        job = app.jobs.spawn("sh -c 'true'")
        await job.wait()
        job.command = "porthole flash boot"
        drawer.attach(job)
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRun)
    tui_harness.pilot(make, body)


# -- Ruling R25: the drawer's stream dies silently at 20,000 lines --------
#
# `len(job.lines)` measures the RING (a deque(maxlen=20000)), not the
# stream: once it saturates, every append evicts the oldest line and len()
# is pinned at maxlen forever, so a `len(lines) > self._seen` guard never
# fires again. The collapsed label keeps ticking regardless (it recomputes
# from a fresh snapshot every tick), so the app looks alive while the
# expanded log has been frozen since line 20000. A kernel build is exactly
# the volume that exceeds it. Both tests shrink the ring to make the
# saturation reachable in a test run instead of after 20,000 real lines.
#
# RichLog defers rendering until its size is known, and #job-log is
# `display: none` while the drawer is collapsed (same quirk documented on
# test_output_reaches_the_drawer_while_the_job_runs above) -- so both tests
# expand the drawer and pause before reading its rendered lines.

def _joined(drawer):
    log = drawer.query_one("#job-log", RichLog)
    return "\n".join(strip.text for strip in log.lines)


def test_the_stream_keeps_flowing_past_the_ring_buffer_size():
    async def body(app, pilot):
        drawer = app.screen.query_one(JobDrawer)
        # A `sleep 0.01` between lines (0.6s total) spreads the 60 lines
        # across several of the drawer's 0.1s drain ticks, so the ring
        # saturates and gets drained from PARTWAY through the run -- not
        # caught in one final catch-up read once the job has already
        # finished, which a `len(lines) > self._seen` guard can still pass
        # by accident (all 10 survivors happen to include the last line).
        job = app.jobs.spawn(
            "sh -c 'i=0; while [ $i -lt 60 ]; do echo line $i; sleep 0.01; "
            "i=$((i+1)); done'")
        job.lines = collections.deque(job.lines, maxlen=10)   # shrink the ring
        drawer.attach(job)
        await pilot.pause(0.3)          # let it run partway, mid-stream drains happen
        await job.wait()
        drawer.expanded = True
        await pilot.pause(0.4)
        rendered = _joined(drawer)
        assert "line 59" in rendered, "the stream stopped: {}".format(rendered[-200:])
    tui_harness.pilot(make, body)


def test_lines_lost_to_the_ring_are_reported_not_silently_dropped():
    async def body(app, pilot):
        drawer = app.screen.query_one(JobDrawer)
        job = app.jobs.spawn("sh -c 'i=0; while [ $i -lt 60 ]; do echo line $i; "
                             "i=$((i+1)); done'")
        job.lines = collections.deque(job.lines, maxlen=10)
        await job.wait()
        drawer.attach(job)                    # attach AFTER, so eviction already happened
        drawer.expanded = True
        await pilot.pause(0.4)
        assert "scrolled past" in _joined(drawer)
    tui_harness.pilot(make, body)


# -- Ruling R23: the port filter is a dead affordance that lies -----------

def _snapshot_with(rows):
    # Built the same way test_the_brain_filter_never_hides_the_laws builds
    # its fixed snapshot -- a fabricated Snapshot, not whatever the repo's
    # real device state happens to be, so this cannot pass vacuously.
    from porthole_tui import state as _state
    return _state.Snapshot(device="test-device", devices=[], cfg={},
                           rows=rows, summary={}, tools=[], error=None,
                           stamp=1.0, notes=[])


def test_the_port_filter_actually_filters():
    """`/` opened a filter that accepted text and changed nothing, while the count label
    claimed a filter was active -- the interface asserting something false. Fourth
    instance in this project of a control advertising a behaviour it does not have."""
    async def body(app, pilot):
        app.store.refresh(block=True)
        await pilot.pause()
        view = app.screen.query_one(PortView)
        view.snapshot = app.store.snapshot
        await pilot.pause()
        total = len(view.visible_rows())
        assert total > 1, "need milestones to filter"
        view.query = "zzzznope-matches-nothing"
        await pilot.pause()
        assert view.visible_rows() == [], "a filter matching nothing must empty the list"
        assert "0" in view.count_label(), view.count_label()
    tui_harness.pilot(make, body)


def test_the_port_filter_keeps_stale_first():
    # Stale must still lead WITHIN the filtered set: a milestone the probe contradicts is
    # the one thing that must never be pushed below the fold.
    import porthole_milestones as ms
    view = PortView()
    view.snapshot = _snapshot_with(rows=[
        {"id": "a", "phase": "first-boot", "title": "usb gadget", "state": ms.BLOCKED,
         "source": "probe", "evidence": "", "why": "", "how": "", "playbook": "",
         "safe": False},
        {"id": "b", "phase": "first-boot", "title": "usb storage", "state": "done",
         "source": "stale", "evidence": "probe disagrees", "why": "", "how": "",
         "playbook": "", "safe": False},
    ])
    view.query = "usb"
    labels = [label for label, _ in view.visible_rows()]
    assert len(labels) == 2, labels
    assert "stale" in labels[0], "stale must lead within the filtered set: {}".format(labels)


# -- Ruling R24: on_catalogue_chosen discriminates explicitly, not by luck --
#
# The old handler told Tool/Note/device apart by hasattr() alone. Tool and
# Note happen to have disjoint attribute names today, so it worked -- but
# that is luck, not a discriminator, and an unrecognised payload fell through
# to the device branch and got formatted into a `porthole use {...}` command,
# which passes launch()'s prefix gate (it checks the PREFIX, not the sense).

class _Chosen:
    def __init__(self, payload):
        self.payload = payload


def test_choosing_a_milestone_opens_its_reader():
    async def body(app, pilot):
        row = {"id": "m1", "phase": "first-boot", "title": "usb gadget answers",
               "state": "todo", "source": "probe", "evidence": "", "why": "because",
               "how": "porthole doctor", "playbook": "", "safe": True}
        app.screen.on_catalogue_chosen(_Chosen(row))
        await pilot.pause()
        assert isinstance(app.screen, Reader), type(app.screen).__name__
        assert "usb gadget answers" in app.screen.doc.title
    tui_harness.pilot(make, body)


def test_choosing_a_note_opens_its_reader():
    async def body(app, pilot):
        import porthole_cmd_brain as bmod
        note = bmod.load_notes(ROOT)[0]
        app.screen.on_catalogue_chosen(_Chosen(note))
        await pilot.pause()
        assert isinstance(app.screen, Reader)
    tui_harness.pilot(make, body)


def test_choosing_a_device_switches_to_it():
    async def body(app, pilot):
        app.screen.on_catalogue_chosen(_Chosen("google-taimen"))
        await pilot.pause()
        # `use` is safe and harmless, so it spawns without a confirm
        assert any("porthole use google-taimen" == j.command for j in app.jobs.jobs), \
            [j.command for j in app.jobs.jobs]
    tui_harness.pilot(make, body)


def test_an_unrecognised_payload_is_refused_not_formatted_into_a_command():
    # launch() checks the PREFIX, not the sense: a stringified dict would pass its gate.
    async def body(app, pilot):
        app.screen.on_catalogue_chosen(_Chosen({"id": "x", "title": "no phase key"}))
        await pilot.pause()
        assert app.jobs.jobs == [], [j.command for j in app.jobs.jobs]
        assert not isinstance(app.screen, Reader)
    tui_harness.pilot(make, body)


# -- Task 14: interactive verbs get the real terminal ---------------------
#
# A pilot cannot drive a real tty handover (App.suspend() hands the actual
# terminal to a subprocess) -- these two prove ROUTING only: an interactive
# command reaches _suspend_and_run instead of being streamed into the
# drawer, and a streaming command never touches _suspend_and_run at all.
# The tty handover itself is unverified by this suite; see the task report.

def test_interactive_command_routes_through_suspend_not_the_drawer():
    async def body(app, pilot):
        calls = []
        app.screen._suspend_and_run = lambda command: calls.append(command)
        app.screen._spawn("porthole serial console")
        await pilot.pause()
        assert calls == ["porthole serial console"], calls
        assert app.jobs.jobs == [], \
            "an interactive command must not also be streamed into the drawer"
    tui_harness.pilot(make, body)


def test_a_streaming_command_never_touches_suspend():
    async def body(app, pilot):
        calls = []
        app.screen._suspend_and_run = lambda command: calls.append(command)
        app.screen._spawn("porthole brief")
        await pilot.pause()
        assert calls == []
        assert len(app.jobs.jobs) == 1
        app.jobs.cancel_all()
    tui_harness.pilot(make, body)


# -- Ruling R26: Ctrl-D at the suspend prompt wrecks the terminal ----------
#
# Textual 8.2.8's App.suspend() is a @contextmanager with no try/finally --
# read directly from the installed package -- so an exception escaping the
# `with` body skips terminal restoration entirely. _suspend_and_run's body
# must therefore be incapable of raising.
#
# Textual's own headless test driver reports can_suspend = False, so
# app.suspend() itself raises SuspendNotSupported the instant it is entered
# under a pilot -- confirmed directly, and it means these tests cannot enter
# the real contextmanager the way ruling R26's own suggested test assumed.
# app.suspend is swapped for a real (working) contextmanager just for the
# duration of each test so _suspend_and_run's own body -- the part this repo
# controls -- actually runs and is what gets exercised.

def test_the_suspend_prompt_survives_ctrl_d():
    """Ctrl-D at the prompt is an ordinary habit, and it must not leave the
    user in raw mode with no way back."""
    import builtins
    import contextlib
    import subprocess as sp

    @contextlib.contextmanager
    def working_suspend():
        yield

    async def body(app, pilot):
        real_input, real_run, real_suspend = builtins.input, sp.run, app.suspend
        builtins.input = lambda *a: (_ for _ in ()).throw(EOFError())
        sp.run = lambda *a, **k: None
        app.suspend = working_suspend
        try:
            app.screen._suspend_and_run("porthole brief")   # must not raise
        finally:
            builtins.input, sp.run, app.suspend = real_input, real_run, real_suspend
        assert app.is_running
    tui_harness.pilot(make, body)


def test_suspend_expands_the_tilde_argv_never_reaches_the_tool_unexpanded():
    """jobs.py's own _run() expands `~` because exec has no shell -- "a
    literal `~` would reach the tool unexpanded". This path builds argv the
    same way and must not skip it (ruling R26, item 2)."""
    import builtins
    import contextlib
    import os
    import subprocess as sp

    @contextlib.contextmanager
    def working_suspend():
        yield

    async def body(app, pilot):
        captured = []
        real_input, real_run, real_suspend = builtins.input, sp.run, app.suspend
        builtins.input = lambda *a: ""
        sp.run = lambda argv, **k: captured.append(argv)
        app.suspend = working_suspend
        try:
            app.screen._suspend_and_run(
                "porthole serial console --log ~/x.log")
        finally:
            builtins.input, sp.run, app.suspend = real_input, real_run, real_suspend
        assert captured, "subprocess.run was never called"
        assert "~/x.log" not in captured[0], captured[0]
        assert os.path.expanduser("~/x.log") in captured[0], captured[0]
    tui_harness.pilot(make, body)


# -- Task 15: the argument form and the file picker -----------------------
#
# The old palette built `porthole <verb>` for every verb and stopped there,
# so it could reach a verb's default behaviour and nothing past it:
# `porthole blobs unsparse vendor.img` was unreachable, and there was no
# filesystem navigation anywhere in the application.

def spec_for(verb):
    import porthole_cli
    return {s["verb"]: s for s in porthole_cli.discover(ROOT)}[verb]


def test_the_form_builds_the_command_as_you_fill_it():
    async def body(app, pilot):
        form = ArgForm(spec_for("blobs"))
        app.push_screen(form)
        await pilot.pause()
        form.values["action"] = "unsparse"
        form.values["path"] = "~/dl/vendor.img"
        form.values["out"] = "vendor.raw.img"
        assert form.command() == (
            "porthole blobs unsparse ~/dl/vendor.img --out vendor.raw.img"), \
            form.command()
    tui_harness.pilot(make, body)


def test_an_empty_form_is_still_a_valid_command():
    async def body(app, pilot):
        form = ArgForm(spec_for("blobs"))
        app.push_screen(form)
        await pilot.pause()
        assert form.command() == "porthole blobs"
    tui_harness.pilot(make, body)


def test_pressing_an_example_fills_every_field():
    async def body(app, pilot):
        spec = spec_for("blobs")
        form = ArgForm(spec)
        app.push_screen(form)
        await pilot.pause()
        form.use_example(spec["examples"][0])
        await pilot.pause()
        assert form.command() == spec["examples"][0], form.command()
    tui_harness.pilot(make, body)


def test_every_verb_opens_a_form_without_crashing():
    # The old palette could reach a verb's default and nothing past it. This
    # asserts the replacement can open all of them.
    import porthole_cli

    async def body(app, pilot):
        for spec in porthole_cli.discover(ROOT):
            form = ArgForm(spec)
            app.push_screen(form)
            await pilot.pause()
            assert form.command().startswith("porthole " + spec["verb"]), \
                spec["verb"]
            app.pop_screen()
            await pilot.pause()
    tui_harness.pilot(make, body)


def test_shell_snippet_examples_are_not_offered_as_try_rows():
    """5 of this project's 107 documented examples are shell snippets
    (`cd "$(porthole cd)"`, `porthole completion bash > file`) that cannot
    decompose into form fields -- argspec.is_direct() says so. A clickable
    "try:" row that silently does nothing when pressed is the exact defect
    class (R20) this project keeps shipping, so a verb whose examples are
    ALL shell snippets must render no "try:" rows, and no "try:" heading
    dangling over nothing, either."""
    async def body(app, pilot):
        form = ArgForm(spec_for("cd"))
        app.push_screen(form)
        await pilot.pause()
        buttons = list(form.query(".form--example"))
        assert buttons == [], [str(b.label) for b in buttons]
        headings = [str(s.content) for s in form.query(Static)]
        assert "try:" not in headings, headings
    tui_harness.pilot(make, body)


def test_direct_examples_are_still_offered():
    # The is_direct() filter must not swallow everything: every one of
    # blobs' examples is a real verb invocation.
    async def body(app, pilot):
        spec = spec_for("blobs")
        form = ArgForm(spec)
        app.push_screen(form)
        await pilot.pause()
        buttons = list(form.query(".form--example"))
        assert len(buttons) == len(spec["examples"]), len(buttons)
    tui_harness.pilot(make, body)


def test_ctrl_s_submits_the_form_through_the_boundary():
    # Proves ArgForm's Binding("ctrl+s", "submit") -> action_submit really
    # fires, via the actual key, and dismisses the built command.
    async def body(app, pilot):
        form = ArgForm(spec_for("blobs"))
        result = []
        app.push_screen(form, result.append)
        await pilot.pause()
        form.values["action"] = "ls"
        expected = form.command()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert result == [expected], result
    tui_harness.pilot(make, body)


def test_escape_cancels_the_form():
    # Proves ArgForm's Binding("escape", "cancel") -> action_cancel really
    # fires, and dismisses None rather than a half-built command.
    async def body(app, pilot):
        form = ArgForm(spec_for("blobs"))
        result = []
        app.push_screen(form, result.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert result == [None], result
    tui_harness.pilot(make, body)


def test_escape_cancels_the_file_picker():
    # Proves FilePicker's Binding("escape", "cancel") -> action_cancel.
    async def body(app, pilot):
        picker = FilePicker(start=ROOT)
        result = []
        app.push_screen(picker, result.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert result == [None], result
    tui_harness.pilot(make, body)


def test_ctrl_s_chooses_the_typed_path_in_the_picker():
    # Proves FilePicker's Binding("ctrl+s", "choose") -> action_choose.
    async def body(app, pilot):
        picker = FilePicker(start=ROOT)
        result = []
        app.push_screen(picker, result.append)
        await pilot.pause()
        picker.query_one("#picker-path", Input).value = "/some/typed/path"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert result == ["/some/typed/path"], result
    tui_harness.pilot(make, body)


def test_the_picker_starts_at_porthole_workdir_not_wherever_the_terminal_is():
    """The image you want is next to the port, not next to wherever the
    terminal happens to be: PORTHOLE_WORKDIR wins when set, then the
    device's own resolved workdir, then cwd -- never blindly cwd."""
    from porthole_tui import state as _state

    def snap(cfg=None, device="d", device_paths=None):
        return _state.Snapshot(device=device, devices=[], cfg=cfg or {},
                               rows=[], summary={}, tools=[], error=None,
                               stamp=1.0, notes=[],
                               device_paths=device_paths or {})

    assert _picker_start(snap(cfg={"PORTHOLE_WORKDIR": "/vendor/tree"})) \
        == "/vendor/tree"
    assert _picker_start(snap(device_paths={"d": {"workdir": "/dev/tree"}})) \
        == "/dev/tree"
    assert _picker_start(snap()) == pathlib.Path.cwd()
    assert _picker_start(None) == pathlib.Path.cwd()


def test_pressing_e_opens_the_argument_form_for_the_selection():
    # Proves MainScreen's Binding("e", "edit_args") -> action_edit_args
    # really fires, via the actual key, for a real milestone from the real
    # registry -- not a call to the method directly.
    async def body(app, pilot):
        app.store.refresh(block=True)
        view = app.screen.query_one(PortView)
        view.snapshot = app.store.snapshot
        await pilot.pause()
        rows = view.visible_rows()
        index = next((i for i, (_label, row) in enumerate(rows)
                      if (row.get("how") or "").startswith("porthole ")), None)
        if index is None:
            return                      # no runnable milestone in this checkout
        verb = rows[index][1]["how"].split()[1]
        listing = view.query_one("#rows")
        listing.index = index
        listing.focus()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ArgForm), type(app.screen).__name__
        assert app.screen.spec["verb"] == verb
    tui_harness.pilot(make, body)


def test_the_blobs_form_is_the_acceptance_test_for_the_whole_plan():
    """Task 15 brief, Step 7, driven headlessly. `blobs` must show its
    description, an ACTION select carrying all five actions, a PATH field
    with a working browse button, and a live preview that reaches
    `porthole blobs unsparse <path>` once both are set -- via the real
    widgets and the real message pipeline, not by poking form.values."""
    async def body(app, pilot):
        spec = spec_for("blobs")
        form = ArgForm(spec)
        app.push_screen(form)
        await pilot.pause()

        # the description renders
        texts = [str(s.content) for s in form.query(Static)]
        assert spec["description"] in texts, texts

        # the ACTION select carries all five actions
        select = form.query_one("#f-action", Select)
        choices = {value for _label, value in select._options
                  if value is not Select.NULL}
        assert choices == {"unsparse", "unpack", "ls", "extract", "inventory"}, \
            choices

        # a PATH field with a working browse button
        assert form.query_one("#f-path", Input) is not None
        assert form.query_one("#b-path", Button) is not None

        preview = form.query_one("#form-command", Static)
        assert str(preview.content) == "porthole blobs", str(preview.content)

        # choosing the action through the REAL Select value pipeline (not
        # form.values directly) is what would catch a _repaint() that never
        # recomputes.
        select.value = "unsparse"
        await pilot.pause()
        assert str(preview.content) == "porthole blobs unsparse", \
            str(preview.content)

        # browse, rooted at the resolved start dir -- never blindly cwd
        await pilot.click("#b-path")
        await pilot.pause()
        assert isinstance(app.screen, FilePicker), type(app.screen).__name__
        expected = pathlib.Path(_picker_start(app.store.snapshot)).expanduser()
        assert app.screen.start == expected, (app.screen.start, expected)

        app.screen.query_one("#picker-path", Input).value = "/tmp/vendor.img"
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert isinstance(app.screen, ArgForm), type(app.screen).__name__
        assert str(preview.content) == "porthole blobs unsparse /tmp/vendor.img", \
            str(preview.content)
    tui_harness.pilot(make, body)


# -- Task 16: the palette, rebuilt -----------------------------------------
#
# The old palette built `porthole <verb>` for every verb and ran it, so it
# reached a verb's default and nothing past it -- `blobs unsparse vendor.img`
# was unreachable. score()/rank() are ported verbatim from the deleted curses
# palette (git show 810b153:lib/porthole_tui/panes/palette.py); what changed
# is Enter, which now opens a form for anything that takes arguments.

def test_ranking_prefers_an_exact_label():
    items = [{"label": "suspend-cycle", "detail": ""},
             {"label": "suspend", "detail": ""}]
    assert pal.rank(items, "suspend")[0]["label"] == "suspend"


def test_ranking_is_not_fuzzy_enough_to_offer_a_flash_by_accident():
    # A tool that guesses too eagerly offers to flash something when you
    # meant to read a note. Exactness is cheap here and the failure is
    # expensive.
    items = [{"label": "flash", "detail": ""}]
    assert pal.rank(items, "note") == []


def test_ranking_matches_a_subsequence():
    # "sspc" -> "suspend-cycle" (ruling R19, ACTION 3). score() has an
    # explicit subsequence branch and nothing else exercises it, so it could
    # be deleted silently.
    items = [{"label": "suspend-cycle", "detail": ""}, {"label": "flash", "detail": ""}]
    hits = pal.rank(items, "sspc")
    assert [h["label"] for h in hits] == ["suspend-cycle"], hits


def test_a_verb_that_takes_arguments_is_marked_as_such():
    items = pal.collect(ROOT, None)
    blobs = next(i for i in items
                if i["kind"] == "verb" and i["label"] == "blobs")
    assert blobs["needs_args"] is True


def test_a_verb_with_no_arguments_is_not():
    items = pal.collect(ROOT, None)
    version = next(i for i in items
                  if i["kind"] == "verb" and i["label"] == "version")
    assert version["needs_args"] in (True, False)


def test_every_namespace_gets_a_badge():
    for kind in ("verb", "tool", "note", "milestone"):
        assert pal.BADGE[kind].isupper()


def test_the_palette_reaches_all_four_namespaces():
    # Ruling R19, ACTION 3. A porter does not think "is what I want a verb
    # or a tool", they think "suspend". collect() builds all four; nothing
    # else asserts all four are actually present.
    kinds = {i["kind"] for i in pal.collect(ROOT, None)}
    assert {"verb", "tool", "note"} <= kinds, kinds
    # milestones need a snapshot with rows; collect() adds them from snap.rows


def test_choosing_a_verb_with_arguments_opens_its_form():
    async def body(app, pilot):
        await pilot.press("ctrl+p")
        await pilot.pause()
        palette = app.screen
        assert isinstance(palette, pal.Palette), type(palette).__name__
        chosen = next(i for i in palette.items
                      if i["kind"] == "verb" and i["label"] == "blobs")
        palette.dismiss(chosen)
        await pilot.pause()
        assert isinstance(app.screen, ArgForm), type(app.screen).__name__
        assert app.jobs.jobs == [], "a bare `porthole blobs` must never spawn"
    tui_harness.pilot(make, body)


def main():
    return tui_harness.run(globals())


if __name__ == "__main__":
    sys.exit(main())
