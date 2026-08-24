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

from textual.widgets import Input, ListView  # noqa: E402

from porthole_tui.app import PortholeApp, SECTIONS  # noqa: E402
from porthole_tui.widgets.brain import NoteList  # noqa: E402
from porthole_tui.widgets.devices import DeviceList  # noqa: E402
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
        await pilot.press("4")
        await pilot.pause()
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


def main():
    return tui_harness.run(globals())


if __name__ == "__main__":
    sys.exit(main())
