#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The console, tested without a terminal.

Curses cannot be driven in CI, so the split is deliberate: every decision a pane
makes is a pure function of the snapshot, and `app.py` holds only the calls that
need a real screen. Panes render into a recording fake here, which covers layout,
colour roles and content. What stays untested is the curses binding, which is
stdlib.

The most important test in this file is the safety one. A TUI that can flash a
device is a TUI that can brick one by mis-keystroke, and "we were careful" is
not a mechanism.
"""
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole  # noqa: E402
import porthole_milestones as ms  # noqa: E402


class FakeWin:
    """Records what would have been drawn, and composes it into a real grid.

    The grid matters: a pane can pass every content assertion and still print
    "enter runs i" because a hard-coded x offset overran the width. Only laying
    the characters out where curses would put them catches that, and a real
    terminal is not available in CI.
    """

    def __init__(self, height=24, width=100):
        self.calls = []
        self.height, self.width = height, width
        self.grid = [[" "] * width for _ in range(height)]

    def addstr(self, y, x, text, attr=0):
        self.calls.append((y, x, text, attr))
        if 0 <= y < self.height:
            for i, ch in enumerate(text):
                if 0 <= x + i < self.width:
                    self.grid[y][x + i] = ch

    @property
    def text(self):
        return "\n".join(c[2] for c in self.calls)

    @property
    def screen(self):
        return "\n".join("".join(r).rstrip() for r in self.grid)


def snapshot(device="google-taimen", rows=None, summary=None, **kw):
    from porthole_tui.model import Snapshot
    cfg = porthole.load_config(root=ROOT, env={"PORTHOLE_DEVICE": device})
    base = dict(device=device, devices=porthole.list_profiles(ROOT), cfg=cfg,
                rows=rows if rows is not None else [], summary=summary or {},
                has_markers=True, pmaports="", pmaports_branch="",
                workdir=cfg.get("PORTHOLE_WORKDIR", ""), soc=cfg.get("PORTHOLE_SOC", ""),
                state="unprobed", error=None, stamp=0.0)
    base.update(kw)
    return Snapshot(**base)


def row(id="x", phase="first boot", title="A milestone", state=ms.TODO,
        source="derived", evidence="", why="because", how="porthole doctor",
        safe=True):
    return dict(id=id, phase=phase, title=title, state=state, source=source,
                evidence=evidence, ticked=False, why=why, how=how, safe=safe)


# ------------------------------------------------------------------ safety --

def test_an_irreversible_command_is_confirmed_even_when_marked_safe():
    """The belt-and-braces rule: `safe` is a human judgement in a table, and a
    table can be edited in a hurry. The command text is screened independently,
    and either veto is enough."""
    from porthole_tui.app import needs_confirmation
    for command in ("porthole run tk-flash-boot.sh img",
                    "porthole run fastboot-something",
                    "porthole sandbox install",
                    "porthole run tk-reboot.sh"):
        assert needs_confirmation(command, safe=True), (
            f"{command!r} would have run unprompted despite being irreversible")


def test_an_unsafe_step_is_confirmed_even_when_it_looks_harmless():
    """Unsafe covers "long and attention-needing" as well as "destructive".
    `porthole run boot-probe.sh` destroys nothing and still should not fire
    from a stray keypress."""
    from porthole_tui.app import needs_confirmation
    assert needs_confirmation("porthole run boot-probe.sh", safe=False)
    assert needs_confirmation("porthole aports build", safe=False)


def test_a_safe_harmless_command_runs_without_a_prompt():
    """The rule has to permit something, or the TUI is a menu with an extra
    keystroke."""
    from porthole_tui.app import needs_confirmation
    assert not needs_confirmation("porthole brain search --severity law", True)
    assert not needs_confirmation("porthole soc list", True)


def test_an_empty_command_is_never_run():
    from porthole_tui.app import needs_confirmation
    assert needs_confirmation("", safe=True)


def test_no_safe_milestone_command_is_irreversible():
    """Cross-check the milestone table against the screen, so a table edit
    cannot quietly make something destructive auto-runnable."""
    from porthole_tui.app import needs_confirmation
    for m in ms.MILESTONES:
        if not m.safe or not m.how.startswith("porthole"):
            continue
        assert not needs_confirmation(m.how, safe=True), (
            f"milestone {m.id!r} is marked safe but its command {m.how!r} "
            f"looks irreversible; one of the two is wrong")


# -------------------------------------------------------------------- port --

def test_port_pane_shows_the_next_action():
    from porthole_tui.panes import port
    win = FakeWin()
    port.render(win, snapshot(rows=[row()],
                              summary={"next": {"id": "x", "title": "Do the thing",
                                                "why": "it matters",
                                                "command": "porthole doctor",
                                                "playbook": "", "safe": True,
                                                "evidence": ""},
                                       "progress": {}, "blocked": [], "stale": []}),
                24, 100)
    assert "Do the thing" in win.text
    assert "porthole doctor" in win.text
    assert "enter runs it" in win.text


def test_an_unsafe_next_step_is_not_offered_for_running():
    from porthole_tui.panes import port
    win = FakeWin()
    port.render(win, snapshot(rows=[row()],
                              summary={"next": {"id": "x", "title": "Flash it",
                                                "why": "", "command": "porthole run flash",
                                                "playbook": "", "safe": False,
                                                "evidence": ""},
                                       "progress": {}, "blocked": [], "stale": []}),
                24, 100)
    assert "enter runs it" not in win.text
    assert "needs confirming" in win.text


def test_a_stale_milestone_is_shown_before_anything_else():
    """A port that believes it is further along than it is must never have that
    fact scroll off the screen."""
    from porthole_tui.panes import port
    win = FakeWin()
    rows = [row(id=f"b{i}", state=ms.BLOCKED, evidence="no device")
            for i in range(6)]
    rows.append(row(id="s", source="stale", title="Slot policy",
                    evidence="ticked, but never probed"))
    port.render(win, snapshot(rows=rows, summary={"next": None, "progress": {},
                                                  "blocked": [], "stale": []}),
                24, 100)
    lines = [c[2] for c in win.calls]
    stale_at = next(i for i, l in enumerate(lines) if "stale" in l)
    blocked_at = next(i for i, l in enumerate(lines) if "blocked" in l)
    assert stale_at < blocked_at, "blocked items pushed a stale claim down"


def test_port_pane_survives_no_device():
    from porthole_tui.panes import port
    win = FakeWin()
    port.render(win, snapshot(device="", rows=[], summary={}), 24, 80)
    assert "new-device" in win.text


def test_nothing_is_clipped_at_the_right_edge():
    """The first version right-aligned a label by a guessed offset and printed
    "enter runs i". Only a composed grid catches that."""
    from porthole_tui.panes import port
    for width in (72, 80, 100, 140):
        win = FakeWin(24, width)
        port.render(win, snapshot(rows=[row()], summary={
            "next": {"id": "x", "title": "A title long enough to need trimming "
                                         "on a narrow terminal",
                     "why": "a reason that also runs on for a while",
                     "command": "porthole brain search --severity law",
                     "playbook": "", "safe": True, "evidence": ""},
            "progress": {}, "blocked": [], "stale": []}), 24, width)
        assert "enter runs it" in win.screen, (
            f"the run hint was clipped at width {width}:\n{win.screen}")
        for line in win.screen.splitlines():
            assert len(line) <= width, (
                f"a line overran width {width}: {line!r}")


def test_the_spine_columns_line_up():
    """Ragged columns read as a broken tool, and the phase names are long
    enough to have collided with the bars once already."""
    from porthole_tui.panes import port
    win = FakeWin(24, 100)
    rows = [row(id=f"m{i}", phase=p, state=ms.DONE if i == 0 else ms.TODO)
            for i, p in enumerate(["before the device", "first boot",
                                   "storage, usb, ssh", "packaging"])]
    port.render(win, snapshot(rows=rows, summary={"next": None, "progress": {},
                                                  "blocked": [], "stale": []}),
                24, 100)
    spine = [l for l in win.screen.splitlines()
             if l.strip() and l.strip()[0].isdigit()]
    assert len(spine) == 4, spine
    bars = {l.index("█") if "█" in l else l.index("░") for l in spine}
    assert len(bars) == 1, f"the fill bars start at different columns: {bars}"


# ------------------------------------------------------------------- spine --

def test_the_spine_marks_exactly_one_current_phase():
    from porthole_tui.model import phases
    rows = [row(id="a", phase="p0", state=ms.DONE),
            row(id="b", phase="p1", state=ms.TODO),
            row(id="c", phase="p2", state=ms.TODO)]
    ph = phases(rows)
    assert [p["name"] for p in ph] == ["p0", "p1", "p2"], ph
    assert sum(p["current"] for p in ph) == 1
    assert ph[1]["current"], "the first unfinished phase is where you are"


def test_the_spine_counts_stale_separately():
    from porthole_tui.model import phases
    ph = phases([row(id="a", phase="p", source="stale")])
    assert ph[0]["stale"] == 1


# ----------------------------------------------------------------- palette --

def test_palette_ranks_an_exact_match_first():
    from porthole_tui.panes import palette
    items = [{"kind": "tool", "label": "tk-suspend-cycle.sh", "detail": "", "run": ""},
             {"kind": "verb", "label": "suspend", "detail": "", "run": ""},
             {"kind": "note", "label": "unrelated", "detail": "suspend", "run": ""}]
    ranked = palette.rank(items, "suspend")
    assert ranked[0]["label"] == "suspend"
    assert len(ranked) == 3, "a detail match should still appear"


def test_palette_matches_a_subsequence():
    from porthole_tui.panes import palette
    items = [{"kind": "tool", "label": "suspend-cycle", "detail": "", "run": ""}]
    assert palette.rank(items, "sspc"), "subsequence match failed"


def test_palette_rejects_a_non_match():
    from porthole_tui.panes import palette
    items = [{"kind": "tool", "label": "display", "detail": "panel", "run": ""}]
    assert palette.rank(items, "zzzz") == []


def test_palette_searches_all_four_namespaces():
    from porthole_tui.panes import palette
    kinds = {i["kind"] for i in palette.collect(snapshot(rows=[row()]))}
    assert {"verb", "tool", "note", "milestone"} <= kinds, kinds


# ------------------------------------------------------------------- theme --

def test_every_glyph_has_an_ascii_twin():
    """A minimal bring-up container has an ASCII locale, and mojibake at the
    moment the device will not boot is a failure of the tool, not the terminal."""
    from porthole_tui import theme
    for name, (fancy, plain) in theme._GLYPHS.items():
        assert plain.isascii(), f"{name} has no ASCII fallback"
        assert plain, name


def test_the_bar_is_honest_at_the_edges():
    from porthole_tui import theme
    assert theme.bar(0, 10).count(theme.g("full")) == 0
    assert theme.bar(10, 10).count(theme.g("empty")) == 0
    assert theme.bar(0, 0), "no crash on an empty phase"


def test_fit_never_cuts_mid_word_or_overruns():
    from porthole_tui import theme
    out = theme.fit("a device tree that compiles cleanly", 20)
    assert len(out) <= 20
    assert not out.rstrip(theme.g("ellipsis")).endswith(" ")
    assert theme.fit("short", 20) == "short"
    assert theme.fit("anything", 0) == ""


# ------------------------------------------------------------------- model --

def test_the_model_never_publishes_a_half_built_snapshot():
    """A half-updated model on screen is worse than a stale one: you cannot
    tell which half is which."""
    from porthole_tui.model import Model
    m = Model(ROOT, "google-taimen")
    before = m.snapshot
    m.refresh(block=True)
    after = m.snapshot
    assert after is not before
    assert after.device == "google-taimen"
    assert after.stamp


def test_a_broken_profile_degrades_instead_of_crashing():
    from porthole_tui.model import Model
    m = Model(ROOT, "definitely-not-a-device")
    m.refresh(block=True)
    assert m.snapshot.error, "a bad device should surface as an error, not a crash"


def test_the_first_frame_does_not_need_a_loaded_model():
    """The console showed an empty terminal and then hung, because it built the
    whole model before painting anything. A frame first means it is always
    visibly alive, and a probe that gets slow again degrades to a stale screen
    rather than a black one."""
    from porthole_tui.model import Model
    m = Model(ROOT, "google-taimen")
    snap = m.snapshot                      # never refreshed
    assert not snap.stamp, "an unloaded snapshot should have no timestamp"
    from porthole_tui.panes import port
    win = FakeWin()
    port.render(win, snap, 24, 100)        # must not raise
    assert win.calls, "nothing was drawn for an empty model"


class Screen:
    """A fake stdscr that behaves like curses at the edges, so a frame can be
    driven end to end without a terminal."""

    def __init__(self, h=24, w=100):
        self.h, self.w = h, w
        self.grid = [[" "] * w for _ in range(h)]

    def getmaxyx(self):
        return (self.h, self.w)

    def erase(self):
        self.grid = [[" "] * self.w for _ in range(self.h)]

    def refresh(self):
        pass

    def addstr(self, y, x, text, attr=0):
        import curses
        if not 0 <= y < self.h:
            raise curses.error("addstr out of bounds")
        for i, ch in enumerate(text):
            if 0 <= x + i < self.w:
                self.grid[y][x + i] = ch

    def __str__(self):
        return "\n".join("".join(r).rstrip() for r in self.grid)


def test_the_console_reaches_a_loaded_screen_while_refreshing_in_background():
    """The bug that made the console useless: it drew "reading the port…" and
    never advanced.

    The model was fine and the panes were fine. The event loop used
    `curses.napms()`, which holds the GIL for its entire sleep, so the refresh
    thread was starved and the snapshot never arrived. Nothing raised and
    nothing logged -- the app simply waited forever on a frame it had already
    drawn.

    Driving the real draw loop against a fake screen is what catches that: a
    unit test of the model passes either way, because the model was never the
    problem.
    """
    import time
    from porthole_tui.app import App

    app = App(ROOT, "google-taimen")
    app.model.refresh()                    # background, exactly as run() does
    screen = Screen()
    for _ in range(200):                   # ~10s ceiling, far above the real ~0.1s
        app.draw(screen)
        if app.model.snapshot.stamp:
            break
        time.sleep(0.05)
    assert app.model.snapshot.stamp, (
        "the background refresh never completed while the draw loop ran — "
        "something in the loop is holding the GIL")
    app.draw(screen)
    out = str(screen)
    assert "reading the port" not in out, "still stuck on the loading frame"
    assert "porthole" in out, out[:200]


def test_the_event_loop_does_not_hold_the_gil_while_idling():
    """A source check, because the failure it guards against is invisible:
    napms() blocks with the GIL held and the symptom is a frozen screen with
    no error anywhere."""
    import ast
    import inspect
    from porthole_tui import app as appmod

    # Look for a CALL, not the word: the fix's own comment explains why
    # napms is wrong, and a substring check would trip on the explanation.
    tree = ast.parse(inspect.getsource(appmod.App.run).strip())
    called = {getattr(n.func, "attr", None)
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "napms" not in called, (
        "curses.napms holds the GIL and starves the refresh thread; "
        "use stdscr.timeout() so getch() waits on a read instead")
    assert "timeout" in called, (
        "the loop must wait on getch() with a timeout, not spin")


def test_every_pane_opens_without_crashing():
    """Pane 3 raised TypeError the moment it was opened: it passed the config's
    PORTHOLE_ROOT straight through as a str, and collect() builds `root /
    "tools"`. No test opened a pane through the app, so nothing caught it."""
    from porthole_tui.app import App, PANES
    app = App(ROOT, "google-taimen")
    app.model.refresh(block=True)
    screen = Screen()
    for i, (name, _) in enumerate(PANES):
        app.pane, app.sel = i, 0
        app.draw(screen)                   # must not raise


def test_no_pane_draws_past_the_bottom_of_the_screen():
    """The brain pane drew all 55 notes straight off the screen and let the
    selection run past the end. Every list pane had its own truncation loop,
    which is not a viewport."""
    import curses
    from porthole_tui.app import App, PANES
    app = App(ROOT, "google-taimen")
    app.model.refresh(block=True)
    screen = Screen(24, 100)
    for i, (name, _) in enumerate(PANES):
        app.pane, app.sel = i, 0
        app.draw(screen)
        for _ in range(500):               # hammer well past the end
            app.move(1)
        app.draw(screen)                   # Screen.addstr raises out of bounds
        assert app.sel <= max(0, app.count - 1), (
            f"{name}: selection {app.sel} exceeds {app.count} items")


def test_the_palette_selection_is_clamped_too():
    """text_key did a bare `self.sel += 1`, so holding the arrow key in the
    palette ran the cursor off the end. Clamping lives in one place now."""
    from porthole_tui.app import App
    app = App(ROOT, "google-taimen")
    app.model.refresh(block=True)
    app.in_palette = True
    screen = Screen()
    app.draw(screen)
    for _ in range(1000):
        app.move(1)
    app.draw(screen)
    assert app.sel <= max(0, app.count - 1)


def test_the_viewport_keeps_the_selection_visible():
    from porthole_tui.panes import _list
    items = list(range(55))
    for sel in (0, 1, 20, 54):
        visible, offset, clamped = _list.window(items, sel, 17)
        assert clamped == sel
        assert offset <= sel < offset + len(visible), (
            f"selection {sel} is outside the drawn window {offset}.."
            f"{offset + len(visible)}")
        assert len(visible) <= 17


def test_the_viewport_survives_an_empty_list_and_a_stale_selection():
    """A filter can empty a list under a cursor that was pointing at row 40."""
    from porthole_tui.panes import _list
    assert _list.window([], 40, 10) == ([], 0, 0)
    visible, offset, sel = _list.window([1, 2], 40, 10)
    assert sel == 1 and offset == 0 and visible == [1, 2]


def test_a_long_list_says_where_you_are():
    from porthole_tui.panes import _list
    assert _list.scrollbar(0, 17, 114) == "1-17 of 114"
    assert _list.scrollbar(0, 10, 10) == "", "no indicator when it all fits"


def test_the_session_clock_is_monotonic():
    from porthole_tui.model import Model
    m = Model(ROOT)
    assert m.uptime() >= 0


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
