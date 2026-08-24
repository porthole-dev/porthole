#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The warm model and the Doc builders. No terminal, no textual."""
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_milestones as ms  # noqa: E402
from porthole_tui import state  # noqa: E402


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
    assert store.busy is False, "a raising _build must not wedge the store"


def test_uptime_advances():
    store = state.Store(ROOT)
    first = store.uptime()
    time.sleep(0.01)
    assert store.uptime() > first


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL {}:\n  {}".format(name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("ERROR {}: {}: {}".format(name, type(exc).__name__, exc))
    print("{}/{} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
