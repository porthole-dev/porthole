#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Port state: derivation, precedence, and the stale-tick rule.

The rule under test throughout is that **a probe outranks a tick**. It is the
whole reason this subsystem is trustworthy: a progress display that believed a
checkbox over the filesystem would be confidently wrong in the optimistic
direction, which is the worst direction to be wrong in when the next step is
flashing something.
"""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_milestones as ms  # noqa: E402


class FakeCfg:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=""):
        return self.values.get(key, default)

    def source(self, key):
        return "profile" if key in self.values else "default"


class FakeCtx:
    def __init__(self, values=None, root=None):
        self.cfg = FakeCfg(values or {})
        self.root = root or ROOT


def one(probe, values=None, ticks=None, mid="x"):
    """Evaluate a single synthetic milestone."""
    table = ms.MILESTONES
    ms.MILESTONES = [ms.Milestone(mid, "p", "T", probe=probe)]
    try:
        return ms.evaluate(FakeCtx(values), ticks or {})[0]
    finally:
        ms.MILESTONES = table


# ------------------------------------------------------------- precedence --

def test_a_probe_that_proves_it_wins_over_an_unticked_box():
    row = one(lambda ctx: ms.done("proof"), ticks={"x": False})
    assert row["state"] == ms.DONE
    assert row["source"] == "derived"
    assert row["evidence"] == "proof"


def test_a_ticked_box_the_probe_contradicts_is_reported_stale():
    """The rule this whole module exists for."""
    row = one(lambda ctx: ms.todo("the key is empty"), ticks={"x": True})
    assert row["state"] == ms.TODO, "the probe must win"
    assert row["source"] == "stale"
    assert "empty" in row["evidence"]


def test_a_milestone_no_probe_can_see_falls_back_to_the_tick():
    assert one(lambda ctx: ms.unknown(), ticks={"x": True})["state"] == ms.DONE
    assert one(lambda ctx: ms.unknown(), ticks={"x": True})["source"] == "manual"
    assert one(lambda ctx: ms.unknown(), ticks={"x": False})["state"] == ms.TODO


def test_a_tick_can_record_something_now_unobservable():
    """A device that has gone away does not un-happen a first boot."""
    row = one(lambda ctx: ms.blocked("device unreachable"), ticks={"x": True})
    assert row["state"] == ms.DONE
    assert row["source"] == "manual"


def test_blocked_without_a_tick_stays_blocked():
    row = one(lambda ctx: ms.blocked("device unreachable"), ticks={"x": False})
    assert row["state"] == ms.BLOCKED
    assert row["evidence"] == "device unreachable"


# ---------------------------------------------------------------- summary --

def test_blocked_is_never_chosen_as_next():
    """You cannot pick up a task whose precondition is missing. Offering one
    as the next action is how a tool teaches people to ignore it."""
    table = ms.MILESTONES
    ms.MILESTONES = [
        ms.Milestone("a", "p", "blocked one", probe=lambda c: ms.blocked("no device")),
        ms.Milestone("b", "p", "doable one", probe=lambda c: ms.todo()),
    ]
    try:
        summary = ms.summarise(ms.evaluate(FakeCtx(), {}))
    finally:
        ms.MILESTONES = table
    assert summary["next"]["id"] == "b"
    assert [x["id"] for x in summary["blocked"]] == ["a"]


def test_progress_counts_only_what_is_done():
    table = ms.MILESTONES
    ms.MILESTONES = [
        ms.Milestone("a", "p", "t", probe=lambda c: ms.done("e")),
        ms.Milestone("b", "p", "t", probe=lambda c: ms.todo()),
        ms.Milestone("c", "p", "t", probe=lambda c: ms.blocked("r")),
    ]
    try:
        p = ms.summarise(ms.evaluate(FakeCtx(), {}))["progress"]
    finally:
        ms.MILESTONES = table
    assert (p["done"], p["total"]) == (1, 3), p


def test_a_finished_port_has_no_next():
    table = ms.MILESTONES
    ms.MILESTONES = [ms.Milestone("a", "p", "t", probe=lambda c: ms.done("e"))]
    try:
        assert ms.summarise(ms.evaluate(FakeCtx(), {}))["next"] is None
    finally:
        ms.MILESTONES = table


# -------------------------------------------------------------- checklist --

def test_markers_round_trip():
    text = ms.render_checklist("google-x", {"soc": True})
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "checklist.md"
        path.write_text(text)
        ticks = ms.read_ticks(path)
    assert ticks["soc"] is True
    assert ticks["read-laws"] is False
    assert set(ticks) == {m.id for m in ms.MILESTONES}


def test_an_item_with_no_marker_produces_no_signal():
    """A checklist predating markers must yield "no manual signal", not a
    wrong one. Guessing here would invent progress."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "c.md"
        path.write_text("- [x] Something that was never in the table\n")
        assert ms.read_ticks(path) == {}


def test_legacy_ticks_are_carried_over_and_misses_are_reported():
    """Silently losing a tick would make the migration untrustworthy, and it
    runs exactly once per port."""
    legacy = ("- [x] Read the laws, ten notes worth reading\n"
              "- [x] Something entirely unrelated to any milestone here\n"
              "- [ ] SoC identified in the profile\n")
    ticks, unmatched = ms.match_legacy_ticks(legacy)
    assert ticks.get("read-laws") is True
    assert "soc" not in ticks, "an unticked item must not become ticked"
    assert len(unmatched) == 1
    assert "unrelated" in unmatched[0]


def test_render_is_stable():
    """Regenerating twice must not produce a diff, or every regeneration is
    noise in review."""
    a = ms.render_checklist("google-x", {"soc": True})
    b = ms.render_checklist("google-x", {"soc": True})
    assert a == b


# ----------------------------------------------------------------- probes --

def test_a_template_default_is_not_an_established_value():
    """PORTHOLE_REBOOT_BUDGET_S ships as "120". Reporting that as a MEASURED
    budget is the exact optimistic lie this module exists to prevent."""
    ms._TEMPLATE_CACHE.clear()
    default = ms._template_defaults(FakeCtx()).get("PORTHOLE_REBOOT_BUDGET_S")
    assert default, "the template should ship a default for this key"
    row = one(ms.probe_measured_budget,
              {"PORTHOLE_REBOOT_BUDGET_S": default})
    assert row["state"] == ms.TODO, "a shipped default is not a measurement"
    assert "template default" in row["evidence"]
    row = one(ms.probe_measured_budget, {"PORTHOLE_REBOOT_BUDGET_S": "247"})
    assert row["state"] == ms.DONE


def test_slot_policy_does_not_nag_a_device_without_slots():
    """Reporting todo forever for something physically absent is how a
    checklist teaches people to skim it."""
    row = one(ms.probe_slot_policy, {"PORTHOLE_HAS_AB_SLOTS": "0"})
    assert row["state"] == ms.DONE
    row = one(ms.probe_slot_policy, {"PORTHOLE_HAS_AB_SLOTS": "1"})
    assert row["state"] == ms.TODO


def test_new_silicon_with_no_sibling_is_done_not_todo():
    """New silicon has no sibling BY DEFINITION, and that is the case this
    toolkit exists for. "todo" would imply there is something to go and find."""
    row = one(ms.probe_sibling, {"PORTHOLE_SOC": "notasoc-9000",
                                 "PORTHOLE_DEVICE": "acme-thing"})
    assert row["state"] in (ms.DONE, ms.BLOCKED), row
    if row["state"] == ms.DONE:
        assert "new silicon" in row["evidence"]


def test_no_probe_touches_the_device():
    """`brief` calls this, `brief --no-device` must work offline, and a probe
    that hangs on a dead phone would make the first command an agent runs the
    one that hangs."""
    import inspect
    for m in ms.MILESTONES:
        src = inspect.getsource(m.probe) if m.probe.__name__ != "<lambda>" else ""
        for banned in ("subprocess.run", "socket", "ssh", "fastboot", "adb"):
            assert banned not in src, f"{m.id}'s probe references {banned}"


def test_every_milestone_is_well_formed():
    seen = set()
    for m in ms.MILESTONES:
        assert m.id not in seen, f"duplicate milestone id {m.id}"
        seen.add(m.id)
        assert m.id.replace("-", "").isalnum(), m.id
        assert m.title and m.why, f"{m.id} has no title or no why"
        assert m.phase in ms.PHASES


def test_unsafe_milestones_are_never_offered_to_run():
    """The boundary: `next` may offer a safe host command. Anything that
    flashes or touches the device is printed and never offered."""
    for m in ms.MILESTONES:
        if m.safe:
            continue
        assert m.id in {"verify", "first-boot", "storage", "suspend", "builds"}, (
            f"{m.id} is marked unsafe; confirm that is deliberate")
    for m in ms.MILESTONES:
        if m.safe and m.how.startswith("porthole"):
            assert "flash" not in m.how, f"{m.id} offers to flash"


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
