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

from porthole_tui.app import PortholeApp, SECTIONS  # noqa: E402
from porthole_tui.widgets.rail import Rail  # noqa: E402


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


def test_tab_cycles_focus_and_never_leaves_nothing_focused():
    async def body(app, pilot):
        for _ in range(6):
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused is not None, "focus must never be nowhere"
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


def main():
    return tui_harness.run(globals())


if __name__ == "__main__":
    sys.exit(main())
