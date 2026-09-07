#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The warm model and the Doc builders. No terminal, no textual."""
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

import porthole_milestones as ms  # noqa: E402
from porthole_tui import state, content  # noqa: E402


def test_phases_fold_rows_into_the_spine():
    rows = [
        {"phase": "first-boot", "state": ms.DONE, "source": "probe"},
        {"phase": "first-boot", "state": ms.BLOCKED, "source": "probe"},
        {"phase": "storage", "state": ms.DONE, "source": "stale"},
    ]
    ph = state.phases(rows)
    assert [p["name"] for p in ph] == ["first-boot", "storage"], ph
    assert ph[0]["total"] == 2 and ph[0]["done"] == 1 and ph[0]["blocked"] == 1
    assert ph[1]["stale"] == 1


def test_exactly_one_phase_is_current():
    rows = [
        {"phase": "a", "state": ms.DONE, "source": "probe"},
        {"phase": "b", "state": ms.BLOCKED, "source": "probe"},
        {"phase": "c", "state": ms.BLOCKED, "source": "probe"},
    ]
    ph = state.phases(rows)
    assert [p["current"] for p in ph] == [False, True, False], ph


def test_phases_of_nothing_is_empty_not_an_error():
    assert state.phases([]) == []


def test_a_fresh_store_has_a_usable_empty_snapshot():
    # The first frame paints BEFORE the model loads. A snapshot that is None
    # there means the first frame is a crash, which is what "reading the port"
    # exists to avoid.
    store = state.Store(ROOT)
    snap = store.snapshot
    assert snap.stamp == 0.0
    assert snap.devices == [] and snap.rows == [] and snap.tools == []
    assert snap.error is None


def test_refresh_publishes_a_stamped_snapshot():
    store = state.Store(ROOT)
    store.refresh(block=True)
    assert store.snapshot.stamp > 0


def test_a_broken_build_degrades_into_the_snapshot_not_an_exception():
    # A broken profile or an absent pmaports must degrade the display, never
    # take the session down mid-port.
    store = state.Store(ROOT)

    def boom():
        raise RuntimeError("pmaports is on fire")

    store._build = boom
    store.refresh(block=True)
    assert store.snapshot.error
    assert "pmaports is on fire" in store.snapshot.error
    assert store.snapshot.stamp > 0, "an errored snapshot is still a snapshot"
    # NOT a claim about the finally: -- the except branch below already guaranteed this
    # long before it existed. What this pins is that a raising _build degrades into an
    # error snapshot instead of propagating and taking the session down mid-port.
    assert store.busy is False, "a raising _build must still finish its refresh"


def test_a_raising_publish_step_does_not_wedge_the_store_forever():
    """The real reason Store._load wraps its publish in a try/finally.

    A raising _build was never the risk -- the except branch already fell through and
    cleared _busy. The risk is the publish step itself raising while a refresh is in
    flight: without the finally, _busy stays True for the life of the session and
    refresh()'s `if self._busy: return` guard then rejects EVERY later refresh. The
    console stops updating and never recovers, with nothing on screen to say why.

    _load() is called directly with _busy already set, because that is what an in-flight
    refresh looks like. Going through refresh(block=True) would NOT exercise this: the
    blocking path never sets _busy, so the assertion would pass with the finally removed.
    """
    class Exploding:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            raise RuntimeError("publish step failed")

    store = state.Store(ROOT)
    store._lock = Exploding()
    store._busy = True                 # what a refresh in flight looks like
    try:
        store._load()
    except RuntimeError:
        pass                           # the finally clears the flag, it does not swallow
    assert store.busy is False, "a raising publish step must not wedge the store"


def test_a_refreshed_snapshot_reports_a_real_device_state():
    """The model must actually ASK, not sit at a placeholder.

    state.py exists in its current shape because the header once read UNPROBED
    forever: the model refused to probe at all, while three milestones told
    the user to leave the console and run doctor. The rule was never "no
    device I/O", it was "not on the event loop". Nothing else in the suite
    notices if that regresses.
    """
    store = state.Store(ROOT)
    store.refresh(block=True)
    snap = store.snapshot
    assert snap.state, "state must never be empty after a refresh"
    assert snap.state.lower() != "unprobed", \
        "the model refused to probe: {!r}".format(snap.state)
    assert snap.state.upper() in (
        "BOOTED", "SSH", "FROZEN", "INITRAMFS", "FASTBOOT", "RECOVERY",
        "ABSENT", "UNKNOWN"), \
        "unrecognised device state {!r}".format(snap.state)


def test_device_paths_are_resolved_in_the_model_not_the_widget():
    # Constraint 4: no filesystem work in render. The model resolves them
    # once per refresh (ruling R22).
    store = state.Store(ROOT)
    store.refresh(block=True)
    snap = store.snapshot
    assert isinstance(snap.device_paths, dict)
    for name in snap.devices:
        assert name in snap.device_paths, name
        assert set(snap.device_paths[name]) == {"workdir", "pmaports"}


def test_uptime_advances():
    store = state.Store(ROOT)
    first = store.uptime()
    time.sleep(0.01)
    assert store.uptime() > first


def test_a_stale_milestone_leads_with_the_contradiction():
    # The evidence string IS the answer for a stale milestone. If it is not
    # first, the port believes it is further along than it is -- and that is
    # the direction that gets someone flashing.
    doc = content.for_milestone({
        "title": "usb gadget answers", "state": "done", "source": "stale",
        "evidence": "no g_ether in /sys/class/net", "why": "", "how": "",
        "playbook": "", "safe": False})
    assert "TICKED, BUT THE TOOL DISAGREES" in doc.lines[0]
    assert any("g_ether" in line for line in doc.lines[:5])


def test_a_milestone_carries_its_command_and_its_safety():
    doc = content.for_milestone({
        "title": "probe the panel", "state": "todo", "source": "probe",
        "evidence": "", "why": "nothing else can be tested first",
        "how": "porthole run tk-display-watch", "playbook": "", "safe": True})
    assert doc.command == "porthole run tk-display-watch"
    assert doc.safe is True


def test_an_unknown_tool_is_a_document_not_a_crash():
    doc = content.for_tool(ROOT, "tk-does-not-exist")
    assert doc.lines == ["no such tool"]
    assert doc.command == "" and doc.safe is False


def test_an_unknown_note_is_a_document_not_a_crash():
    doc = content.for_note(ROOT, "no-such-note")
    assert doc.lines == ["no such note"]
    assert doc.command == "" and doc.safe is False


def test_wrap_preserves_blank_lines_and_indented_blocks():
    out = content.wrap("one\n\n    indented stays\n", 40)
    assert "" in out
    assert "    indented stays" in out


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
