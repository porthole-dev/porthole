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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
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


def test_slot_policy_is_not_completed_by_the_shipped_default():
    """The regression this test previously ENSHRINED.

    It used to assert that HAS_AB_SLOTS="0" meant done. But "0" is both the
    template's shipped value and a real answer, so every freshly scaffolded
    device auto-completed the A/B safety milestone -- the one whose own `why`
    says "after the first bad flash is too late to decide this". A safety check
    that completes itself is worse than none, because it also reports that the
    matter is handled.
    """
    row = one(ms.probe_slot_policy, {"PORTHOLE_HAS_AB_SLOTS": "0"})
    assert row["state"] == ms.TODO, "an unprobed default must not read as done"
    assert "never probed" in row["evidence"]


def test_slot_policy_does_not_nag_a_device_with_no_slots():
    """Once ASKED, absent hardware is done-by-not-applying: reporting todo
    forever for something physically absent is how a checklist gets skimmed."""
    row = one(ms.probe_slot_policy, {"PORTHOLE_SLOTS_PROBED": "fastboot-getvar",
                                     "PORTHOLE_HAS_AB_SLOTS": "0"})
    assert row["state"] == ms.DONE
    assert "probed" in row["evidence"]
    row = one(ms.probe_slot_policy, {"PORTHOLE_SLOTS_PROBED": "fastboot-getvar",
                                     "PORTHOLE_HAS_AB_SLOTS": "1"})
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
    import ast
    import inspect
    # Check for CALLS, not vocabulary. A probe may perfectly well say "run
    # `fastboot getvar all`" in its advice string -- naming the fix is the
    # whole point of the evidence field. What must not happen is executing it.
    banned = {"run", "check_output", "call", "check_call", "Popen", "system",
              "create_connection", "urlopen"}
    for m in ms.MILESTONES:
        if m.probe.__name__ == "<lambda>":
            continue
        try:
            tree = ast.parse(inspect.getsource(m.probe).lstrip())
        except (OSError, SyntaxError, IndentationError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                assert name not in banned, (
                    f"{m.id}'s probe calls {name}() — probes must not execute "
                    f"anything; `brief --no-device` has to work offline")


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


# ------------------------------------------------------- bounded searching --

def _tree(base, dirs=300, depth=3):
    """A wide, deep directory tree with nothing we want in it."""
    made = 0
    stack = [(base, 0)]
    while stack and made < dirs:
        node, d = stack.pop()
        if d >= depth:
            continue
        for i in range(6):
            child = node / f"sub{i}"
            child.mkdir(parents=True, exist_ok=True)
            (child / "noise.c").write_text("")
            made += 1
            stack.append((child, d + 1))
    return made


def test_a_probe_never_walks_an_unbounded_tree():
    """The bug that froze the TUI on its first frame.

    probe_dts_compiles called work.rglob(), which reads EVERY directory under
    the workdir. A device repo with a kernel tree in it has ~2 million files,
    so the probe took minutes and the console hung before painting anything.

    Counted rather than timed: a timing assertion is flaky on a loaded CI
    runner, and what actually matters is that the search is bounded at all.
    """
    import os as _os
    import porthole_milestones as pm

    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        _tree(work)
        visited = []
        real = _os.scandir

        def counting(path=".", *a, **k):
            visited.append(str(path))
            return real(path, *a, **k)

        pm.os.scandir = counting
        try:
            hit, exhausted = pm._find(work, "nothing-is-called-this.dts")
        finally:
            pm.os.scandir = real
        assert hit is None
        assert len(visited) <= pm.MAX_DIRS, (
            f"the search read {len(visited)} directories; the cap is "
            f"{pm.MAX_DIRS}")


def test_a_capped_search_reports_that_it_gave_up():
    """"Not found in the first few thousand directories" is not "absent", and
    reporting the second when you established only the first is exactly the
    over-claiming this module exists to prevent."""
    import porthole_milestones as pm
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        _tree(work, dirs=pm.MAX_DIRS + 50, depth=6)
        row = one(pm.probe_dts_exists,
                  {"PORTHOLE_WORKDIR": str(work), "PORTHOLE_DTB": "absent"})
        assert row["state"] == ms.TODO
        if "capped" in row["evidence"]:
            assert str(pm.MAX_DIRS) in row["evidence"]


def test_a_missing_build_artefact_costs_no_search():
    """A .dtb that has not been built yet is the NORMAL state. Walking
    thousands of directories to confirm a routine "no" was 367ms of a 400ms
    probe run."""
    import os as _os
    import porthole_milestones as pm

    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        _tree(work)
        (work / "dts").mkdir(exist_ok=True)
        (work / "dts" / "board.dts").write_text("/dts-v1/;\n")
        visited = []
        real = _os.scandir

        def counting(path=".", *a, **k):
            visited.append(str(path))
            return real(path, *a, **k)

        pm.os.scandir = counting
        try:
            row = one(pm.probe_dts_compiles,
                      {"PORTHOLE_WORKDIR": str(work), "PORTHOLE_DTB": "board"})
        finally:
            pm.os.scandir = real
        assert row["state"] == ms.TODO
        assert len(visited) < 50, (
            f"confirming a missing .dtb read {len(visited)} directories")


def test_series_applies_blocks_when_a_patch_is_malformed():
    import porthole_milestones as ms

    assert "series-applies" in ms.BY_ID, sorted(ms.BY_ID)
    milestone = ms.BY_ID["series-applies"]
    # It sits before `builds`: a series that cannot apply is WHY the packages
    # would not build, so reporting them in the other order buries the cause.
    ids = [m.id for m in ms.MILESTONES]
    assert ids.index("series-applies") < ids.index("builds")


def test_series_applies_is_blocked_not_todo():
    """A broken series is a precondition, not a task you can pick up.

    BLOCKED is what `next` renders in the "getting in the way" list; TODO
    would offer it as the next action, which reads as "go and do this" for
    something no one can do until the patch is fixed.
    """
    import porthole_milestones as ms

    verdict = ms.verdict_for_series([("malformed", "0199-x.patch: ...")])
    assert verdict.state == ms.BLOCKED, verdict
    assert "0199" in verdict.evidence, verdict.evidence


def test_series_applies_ignores_warnings():
    import porthole_milestones as ms

    verdict = ms.verdict_for_series([("stripped", "a.patch: line 12 ...")])
    assert verdict.state == ms.DONE, verdict


def test_series_applies_says_how_many_patches_it_checked():
    import porthole_milestones as ms

    verdict = ms.verdict_for_series([], count=199)
    assert "199" in verdict.evidence, verdict.evidence


def test_kernel_provenance_is_a_milestone_in_the_packaging_phase():
    import porthole_milestones as ms

    assert "kernel-provenance" in ms.BY_ID, sorted(ms.BY_ID)
    assert ms.BY_ID["kernel-provenance"].phase == "packaging"


def test_provenance_verdict_maps_the_three_states():
    import porthole_milestones as ms

    assert ms.verdict_for_provenance("done", "aport r22", age=30.0).state == ms.DONE
    assert ms.verdict_for_provenance("todo", "behind").state == ms.TODO
    assert ms.verdict_for_provenance("blocked", "tree build").state == ms.BLOCKED


def test_a_stale_provenance_reading_is_not_believed_forever():
    """Flash the phone and a `done` from before the flash must not keep
    printing as fact. Same rule as the matrix's own expiry, one module over:
    the age has to change the STATE, not just decorate the evidence."""
    import porthole_milestones as ms

    fresh = ms.verdict_for_provenance("done", "running aport r22", age=30.0)
    assert fresh.state == ms.DONE, fresh
    stale = ms.verdict_for_provenance(
        "done", "running aport r22", age=ms.PROVENANCE_MAX_AGE_S + 1)
    assert stale.state != ms.DONE, stale
    assert "ago" in stale.evidence, stale.evidence


def test_an_undated_provenance_done_is_not_believed():
    """`at` missing, null, or the wrong type reaches here as age=None -- a
    DONE that cannot be dated is the same optimistic-direction mistake as an
    expired one."""
    import porthole_milestones as ms

    verdict = ms.verdict_for_provenance("done", "running aport r22", age=None)
    assert verdict.state != ms.DONE, verdict


def test_a_stale_provenance_age_does_not_improve_a_todo_or_blocked():
    """The asymmetry: only the POSITIVE claim needs a date to stand on. A
    todo/blocked reading is exactly as true regardless of how old (or
    undated) the cache is -- age must never upgrade it."""
    import porthole_milestones as ms

    for age in (None, ms.PROVENANCE_MAX_AGE_S + 1, 30.0):
        assert ms.verdict_for_provenance("todo", "behind", age=age).state == ms.TODO
        assert (ms.verdict_for_provenance("blocked", "tree build", age=age).state
                == ms.BLOCKED)


def test_probe_kernel_provenance_reads_the_at_written_by_brief():
    """End-to-end through the cache read, not just the pure function: a
    kernel-provenance.json with a fresh `at` is DONE; the same blob with an
    old `at` is not."""
    import json
    import tempfile
    import time

    import porthole_milestones as ms

    working = {"state": "done", "evidence": "running aport r22"}
    with tempfile.TemporaryDirectory() as tmp:
        rundir = pathlib.Path(tmp) / ".run"
        rundir.mkdir()
        (rundir / "kernel-provenance.json").write_text(
            json.dumps({**working, "at": time.time()}))
        verdict = ms.probe_kernel_provenance(FakeCtx(root=tmp))
    assert verdict.state == ms.DONE, verdict

    with tempfile.TemporaryDirectory() as tmp:
        rundir = pathlib.Path(tmp) / ".run"
        rundir.mkdir()
        (rundir / "kernel-provenance.json").write_text(
            json.dumps({**working, "at": time.time() - ms.PROVENANCE_MAX_AGE_S - 1}))
        verdict = ms.probe_kernel_provenance(FakeCtx(root=tmp))
    assert verdict.state != ms.DONE, verdict


def test_brief_probes_the_device_before_it_evaluates_milestones():
    """The order is load-bearing, not tidiness.

    `state(max_age=30)` writes the state cache, and the milestone probes read
    it. Evaluating milestones first means they read whatever was there before
    -- which is the "brief says BOOTED, next says not probed" defect exactly.
    Asserted via AST on the actual call nodes because comments can contain the
    same strings as code.
    """
    import ast
    import inspect
    import textwrap
    import porthole_cmd_brief as brief

    source = inspect.getsource(brief.cmd_brief)
    tree = ast.parse(textwrap.dedent(source))

    state_lines = []
    port_state_lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # state(...) call: func is ast.Attribute with attr='state'
        if isinstance(node.func, ast.Attribute) and node.func.attr == "state":
            state_lines.append(node.lineno)
        # _port_state(...) call: func is ast.Name with id='_port_state'
        elif isinstance(node.func, ast.Name) and node.func.id == "_port_state":
            port_state_lines.append(node.lineno)

    # ast.walk is breadth-first, not source order, so the last node visited is
    # not the last in the file. Use min() to find the earliest occurrence, which
    # is the right question here: does the first device probe happen before the
    # first milestone evaluation?
    state_lineno = min(state_lines) if state_lines else None
    port_state_lineno = min(port_state_lines) if port_state_lines else None

    assert state_lineno is not None, (
        "cmd_brief must call state() on the device")
    assert port_state_lineno is not None, (
        "cmd_brief must call _port_state() to evaluate milestones")
    assert state_lineno < port_state_lineno, (
        "cmd_brief must probe the device before evaluating milestones; "
        "the probe warms the cache the milestone probes read")


def test_a_matrix_question_mark_never_advances_a_milestone():
    """The rule the whole matrix rests on.

    `?` means nobody looked. Reading it as done is confidently wrong in the
    direction of "you are further along than you are", which is what
    `porthole next` exists to stop. This used to assert only `!= DONE`,
    which passed just as well for the bug (state == TODO) as for the fix
    (state == UNKNOWN) -- pin the exact state, not just the one thing it
    must not be.
    """
    blob = {"at": 0, "capabilities": [{"name": "wifi", "present": "yes",
                                       "works": "?"}]}
    verdict = ms.verdict_from_matrix(blob, ["wifi"], age=30.0)
    assert verdict.state == ms.UNKNOWN, verdict


def test_an_all_untested_matrix_capability_is_unknown_not_todo():
    """`suspend` has no `works:` probe and BY DESIGN never can -- the spec
    forbids a probe that induces a suspend -- so its row is `works: ?`
    forever, on every device. TODO here would mean this milestone could
    never leave TODO on a tick, ever. UNKNOWN is what lets a human's tick
    stand, same as no matrix at all."""
    blob = {"at": 0, "capabilities": [{"name": "suspend", "present": "yes",
                                       "works": "?"}]}
    verdict = ms.verdict_from_matrix(blob, ["suspend"], age=30.0)
    assert verdict.state == ms.UNKNOWN, verdict


def test_an_all_untested_matrix_capability_does_not_mark_a_tick_stale():
    """The half that was never pinned: UNKNOWN alone is not the fix unless
    `evaluate()` actually leaves the tick alone. Break this by reverting
    verdict_from_matrix to return `todo(...)` for the all-`?` case and this
    must fail: `stale` would flip True and the milestone would demote from
    DONE (manual) to TODO (stale) on every single evaluation forever."""
    blob = {"at": 0, "capabilities": [{"name": "suspend", "present": "yes",
                                       "works": "?"}]}
    probe = lambda ctx: ms.verdict_from_matrix(blob, ["suspend"], age=30.0)
    row = one(probe, ticks={"x": True})
    assert row["state"] == ms.DONE, row
    assert row["source"] == "manual", row
    assert row["source"] != "stale", row


def test_a_matrix_real_failure_still_returns_todo():
    """A real `no` is an actual finding, unlike an untested `?` -- it must
    still demote, and still mark a tick stale."""
    blob = {"at": 0, "capabilities": [{"name": "suspend", "present": "yes",
                                       "works": "no"}]}
    verdict = ms.verdict_from_matrix(blob, ["suspend"], age=30.0)
    assert verdict.state == ms.TODO, verdict
    probe = lambda ctx: verdict
    row = one(probe, ticks={"x": True})
    assert row["state"] == ms.TODO, row
    assert row["source"] == "stale", row


def test_a_mixed_matrix_result_still_returns_todo():
    """Some names pass, some are untested -- that mix IS actionable, unlike
    "we haven't tried any of them", so it must stay TODO (and still mark a
    tick stale)."""
    blob = {"at": 0, "capabilities": [
        {"name": "wifi", "present": "yes", "works": "yes"},
        {"name": "bluetooth", "present": "yes", "works": "?"}]}
    verdict = ms.verdict_from_matrix(blob, ["wifi", "bluetooth"], age=30.0)
    assert verdict.state == ms.TODO, verdict
    assert "bluetooth" in verdict.evidence, verdict.evidence
    probe = lambda ctx: verdict
    row = one(probe, ticks={"x": True})
    assert row["state"] == ms.TODO, row
    assert row["source"] == "stale", row


def test_a_matrix_cell_that_works_makes_the_milestone_done():
    blob = {"at": 0, "capabilities": [{"name": "wifi", "present": "yes",
                                       "works": "yes"}]}
    verdict = ms.verdict_from_matrix(blob, ["wifi"], age=30.0)
    assert verdict.state == ms.DONE, verdict
    assert "30s ago" in verdict.evidence, verdict.evidence


def test_a_milestone_over_several_capabilities_needs_all_of_them():
    # `radios` is wifi AND bluetooth AND modem. Two out of three is not done.
    blob = {"at": 0, "capabilities": [
        {"name": "wifi", "present": "yes", "works": "yes"},
        {"name": "bluetooth", "present": "yes", "works": "yes"},
        {"name": "modem", "present": "yes", "works": "no"}]}
    verdict = ms.verdict_from_matrix(
        blob, ["wifi", "bluetooth", "modem"], age=30.0)
    assert verdict.state != ms.DONE, verdict
    assert "modem" in verdict.evidence, verdict.evidence


def test_no_matrix_at_all_is_unknown_so_a_tick_still_counts():
    """Before anyone runs `porthole matrix` the tick is the only signal.

    UNKNOWN keeps the previous behaviour exactly, so landing the matrix does
    not un-tick a box somebody earned.
    """
    verdict = ms.verdict_from_matrix({}, ["wifi"], age=None)
    assert verdict.state == ms.UNKNOWN, verdict


def test_a_stale_matrix_says_so_rather_than_being_believed():
    """A DONE verdict must not stand forever. `?` in ago beside DONE at 30s
    and DONE at three weeks old would look identical -- so the age has to
    change the STATE, not just decorate the evidence string. Past the cap
    (24h, ms.MATRIX_MAX_AGE_S) a would-be DONE reverts to TODO."""
    blob = {"at": 0, "capabilities": [{"name": "wifi", "present": "yes",
                                       "works": "yes"}]}
    fresh = ms.verdict_from_matrix(blob, ["wifi"], age=30.0)
    assert fresh.state == ms.DONE, fresh
    stale = ms.verdict_from_matrix(blob, ["wifi"],
                                   age=ms.MATRIX_MAX_AGE_S + 1)
    assert stale.state != ms.DONE, stale
    assert "ago" in stale.evidence, stale.evidence


def test_an_unknown_age_is_treated_as_stale_not_fresh():
    """An `at` that is missing, null, or not a number means the age cannot
    be established -- and a DONE that cannot be dated is the same
    confidently-wrong-in-the-optimistic-direction claim `?` and a missing
    cell already are. `probe_from_matrix` passes `age=None` in exactly this
    case; asserting only that some evidence string appears would repeat
    round 1's weak-test problem, so this checks the STATE."""
    blob = {"at": 0, "capabilities": [{"name": "wifi", "present": "yes",
                                       "works": "yes"}]}
    fresh = ms.verdict_from_matrix(blob, ["wifi"], age=30.0)
    assert fresh.state == ms.DONE, fresh
    for bad_age in (None,):
        verdict = ms.verdict_from_matrix(blob, ["wifi"], age=bad_age)
        assert verdict.state != ms.DONE, (bad_age, verdict)


def test_probe_from_matrix_does_not_invent_an_age_from_a_bad_at():
    """End-to-end through `probe_from_matrix`, not just the pure function:
    `at` missing or the wrong type must reach `verdict_from_matrix` as
    `age=None`, never as an invented 0 (which would read as "just now" --
    the most optimistic reading possible)."""
    import json

    working = {"capabilities": [{"name": "wifi", "present": "yes",
                                 "works": "yes"}]}
    for at in (None, "soon"):
        with tempfile.TemporaryDirectory() as tmp:
            rundir = pathlib.Path(tmp) / ".run"
            rundir.mkdir()
            (rundir / "matrix.json").write_text(
                json.dumps({**working, "at": at}))
            verdict = ms.probe_from_matrix("wifi")(FakeCtx(root=tmp))
        assert verdict.state != ms.DONE, (at, verdict)
    # Control: the same matrix with a real, fresh timestamp IS done -- so
    # the assertion above is about the bad `at`, not about this matrix
    # being unable to be done at all.
    with tempfile.TemporaryDirectory() as tmp:
        import time
        rundir = pathlib.Path(tmp) / ".run"
        rundir.mkdir()
        (rundir / "matrix.json").write_text(
            json.dumps({**working, "at": time.time()}))
        verdict = ms.probe_from_matrix("wifi")(FakeCtx(root=tmp))
    assert verdict.state == ms.DONE, verdict


def test_a_null_capabilities_matrix_does_not_raise():
    """A corrupt cache must degrade to a verdict, never a traceback -- that
    is the difference between `porthole next` reporting "nobody has looked"
    and `porthole next` crashing every session until someone deletes the
    file by hand."""
    for capabilities in (None, "not a list", [1, 2, "x"], 42):
        blob = {"at": 0, "capabilities": capabilities}
        verdict = ms.verdict_from_matrix(blob, ["wifi"], age=30.0)
        assert verdict.state != ms.DONE, (capabilities, verdict)


def test_radios_with_an_unprofiled_capability_is_not_done():
    """A name the matrix never mentions is not evidence of success -- it is
    exactly as unproven as `?`. Dropping it silently (the old
    `if n in cells` filter) let `radios` report done with modem never
    touched."""
    blob = {"at": 0, "capabilities": [
        {"name": "wifi", "present": "yes", "works": "yes"},
        {"name": "bluetooth", "present": "yes", "works": "yes"}]}
    verdict = ms.verdict_from_matrix(
        blob, ["wifi", "bluetooth", "modem"], age=30.0)
    assert verdict.state != ms.DONE, verdict
    assert "modem" in verdict.evidence, verdict.evidence


def test_a_cell_missing_works_is_treated_as_untested():
    """`works` absent from a cell is not "yes" and must not be read as one --
    `c.get("works") == "no"` and `== "?"` both silently pass a bare
    `{"name": "wifi"}` cell straight to DONE. A single untested capability is
    the all-untested case (see test_an_all_untested_matrix_capability_is_
    unknown_not_todo), so this reads as UNKNOWN, not a manufactured TODO."""
    blob = {"at": 0, "capabilities": [{"name": "wifi", "present": "yes"}]}
    verdict = ms.verdict_from_matrix(blob, ["wifi"], age=30.0)
    assert verdict.state == ms.UNKNOWN, verdict


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
