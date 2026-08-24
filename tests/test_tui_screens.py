#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The screens, driven by Textual's pilot.

Skips loudly on the 3.8/3.11/3.13 matrix where textual is absent; the `console`
CI job installs it and fails if these skip there.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tui_harness  # noqa: E402

tui_harness.require()

ROOT = pathlib.Path(__file__).resolve().parent.parent

from textual.widgets import Input, ListView, RichLog, Static  # noqa: E402

from porthole_tui.app import PortholeApp, SECTIONS  # noqa: E402
from porthole_tui.screens.confirm import ConfirmRun  # noqa: E402
from porthole_tui.screens.help import HelpScreen  # noqa: E402
from porthole_tui.screens.reader import Reader  # noqa: E402
from porthole_tui.widgets.brain import NoteList  # noqa: E402
from porthole_tui.widgets.devices import DeviceList  # noqa: E402
from porthole_tui.widgets.jobs import JobDrawer  # noqa: E402
from porthole_tui.widgets.port import PortView  # noqa: E402
from porthole_tui.widgets.rail import Rail  # noqa: E402
from porthole_tui.widgets.tools import ToolList  # noqa: E402


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


def main():
    return tui_harness.run(globals())


if __name__ == "__main__":
    sys.exit(main())
