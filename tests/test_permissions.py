#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The allowlist `porthole permissions` writes into an agent's settings.

An allowlist is a security boundary that looks like a config file, which is
the worst kind: nothing goes red when it is wrong, and the failure is an agent
quietly holding a permission nobody granted it.

So these tests are about the boundary, not the rendering. Two questions, over
and over: can a rule reach something irreversible, and can something new
appear on either side of the table without anyone noticing.

Runs with no device and writes only into a temporary directory.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import porthole_cli                     # noqa: E402
import porthole_cmd_permissions as P    # noqa: E402
from _runner import run                 # noqa: E402

DEVICE = "google-taimen"


def test_every_verb_in_the_registry_is_on_one_side_of_the_table():
    """The drift guard, and the reason the deny half exists at all.

    "Not in the allow list" is an accident and reads exactly like a decision.
    A verb added next month must fail HERE, on the day it is added, rather
    than be silently ungranted -- or, far worse, silently granted by someone
    widening a prefix to make their own verb work.

    tests/test_rules.py fails on a MUST with no enforcer for the same reason."""
    known = set(P.ALLOWED_VERBS) | set(P.DENIED_VERBS)
    live = {spec["verb"] for spec in porthole_cli.discover(ROOT)}
    missing = sorted(live - known)
    assert not missing, (
        "these verbs are in neither ALLOWED_VERBS nor DENIED_VERBS, so nobody "
        "has decided about them:\n  " + "\n  ".join(missing)
        + "\nAdd each to one half, with the reason.")
    stale = sorted(known - live)
    assert not stale, (
        "these are in the table but not in the registry:\n  "
        + "\n  ".join(stale))


def test_the_two_halves_do_not_overlap():
    """A verb in both is a table that grants and denies the same thing, and
    the granting half is the one that would win."""
    both = sorted(set(P.ALLOWED_VERBS) & set(P.DENIED_VERBS))
    assert not both, f"granted AND denied: {both}"


def test_nothing_irreversible_is_granted():
    """The claim the whole verb rests on, checked against the rules it emits
    rather than against the tables it builds them from.

    `porthole flash` and the tools that flash, reboot, erase or ramp are the
    named hazards in AGENTS.md section 1 (`confirm-before-irreversible`).
    Every one of them must be absent from the granted set."""
    granted, _held = P.build_rules(ROOT, DEVICE)
    hazards = ["porthole flash", "porthole push", "porthole run",
               "porthole sandbox:", "porthole disk prune",
               "porthole disk retire-host", "tools/ph-flash-boot.sh",
               "tools/ph-reboot.sh", "tools/ph-to-fastboot.sh",
               "tools/ph-thermal-ramp.sh", "tools/ph-recover.sh"]
    leaked = [h for h in hazards
              if any(h in rule for rule in granted)]
    assert not leaked, f"irreversible things reachable from a rule: {leaked}"


def test_the_risky_tools_are_held_back_with_a_reason():
    """Held back is not enough; the preview has to be able to SAY why.

    An allowlist nobody can audit is worse than no allowlist, and "it is not
    in the list" answers no question a reviewer is actually asking."""
    _granted, held = P.build_rules(ROOT, DEVICE)
    for tool in ("tools/ph-flash-boot.sh", "tools/ph-reboot.sh",
                 "tools/ph-to-fastboot.sh"):
        assert tool in held, f"{tool} is neither granted nor explained"
        assert held[tool].startswith("matches "), (
            f"{tool} is held back without naming the word that did it: "
            f"{held[tool]!r}")


def test_the_ordinary_reading_tools_are_granted():
    """THE POSITIVE CONTROL. A `build_rules` that granted nothing would pass
    every assertion above and make the verb useless.

    These three read: state, frame timings, and a register dump."""
    granted, _held = P.build_rules(ROOT, DEVICE)
    for tool in ("tools/ph-sysstate.sh", "tools/ph-fps.py", "tools/ph-pins.py"):
        assert P.rule(tool) in granted, f"{tool} should be granted"
    assert P.rule("porthole brief") in granted
    assert P.rule("git diff") in granted


def test_log_and_disk_are_both_denied_whole_with_disk_report_granted_below():
    """Two verbs with a destructive action, held to the same standard.

    `disk` reshaped its destructive actions from flags to a positional
    ACTION word (`report`/`prune`/`retire-host`) specifically so `disk
    report` could be granted the same way `build status`/`brain search`
    are: a distinct SUBCOMMAND STRING, not a flag riding the same bare
    command line.

    `log` was granted whole once, on the theory that it only ever
    writes/deletes inside .run/ -- but its destructive action is a FLAG
    (`--prune --yes`), not a positional word, so `Bash(porthole log:*)`
    would also match `porthole log --prune --yes`: exactly the shape `disk`
    was reshaped to stop being possible. `log` has had no equivalent reshape,
    so it stays denied whole too, with no positional split to grant a safe
    subset of. The dict-membership half of this is trivial; the real
    property is the prefix-match one below."""
    granted, held = P.build_rules(ROOT, DEVICE)
    assert P.rule("porthole log") not in granted
    assert "porthole log" in held and held["porthole log"]
    assert P.rule("porthole disk report") in granted
    assert "porthole disk" in held and held["porthole disk"]


def test_a_rule_granting_disk_report_does_not_reach_prune_or_retire_host():
    """THE point of the fix-round-3 reshape, checked the way the classifier
    actually checks it: `Bash(x:*)` grants every command line that STARTS
    WITH x. Simulate that against every rule this build emits, for the
    command lines that must never be reachable.

    `porthole log --prune --yes` is here for the same reason: `log` has no
    positional split like `disk`'s, so nothing granted below may start with
    a prefix short enough to also match it."""
    granted, _held = P.build_rules(ROOT, DEVICE)
    dangerous = ["porthole disk prune --yes",
                "porthole disk retire-host --yes --discard-host-workdir",
                "porthole log --prune --yes"]
    for rule_text in granted:
        assert rule_text.startswith("Bash(") and rule_text.endswith(":*)"), (
            rule_text)
        prefix = rule_text[len("Bash("):-len(":*)")]
        for command in dangerous:
            assert not command.startswith(prefix), (
                f"granted rule {rule_text!r} would also match {command!r}")


def test_a_rule_is_a_prefix_pattern_and_nothing_else():
    """`Bash(x:*)` is prefix matching, so a rule's safety is the safety of
    every command line that starts that way. A rule carrying a shell
    metacharacter is one whose match set nobody has actually reasoned about."""
    granted, _held = P.build_rules(ROOT, DEVICE)
    bad = [r for r in granted
           if not (r.startswith("Bash(") and r.endswith(":*)"))
           or any(c in r for c in "|;&$`><\n")]
    assert not bad, f"rules that are not plain prefixes: {bad}"


def test_the_device_shell_is_granted_loudly_and_can_be_declined():
    """Granting the phone's shell is granting the phone. It is a judgement,
    so it must be BOTH the default this repo argues for and one flag away
    from off -- and it goes through the mutex wrapper, never a bare ssh,
    because `Bash(ssh:*)` would grant every host on the network."""
    granted, _held = P.build_rules(ROOT, DEVICE, device_shell=True)
    assert P.rule("tools/ph-device.sh") in granted
    assert not any("Bash(ssh" in r or "Bash(scp" in r for r in granted), (
        "a bare ssh rule grants every host reachable from this laptop")

    off, held = P.build_rules(ROOT, DEVICE, device_shell=False)
    assert P.rule("tools/ph-device.sh") not in off
    assert "tools/ph-device.sh" in held


def test_installing_keeps_the_permissions_already_there():
    """A settings file is where a person keeps their own allowlist and their
    model choice. An install that replaced `permissions.allow` would silently
    revoke rules somebody added by hand, and they would find out one refusal
    at a time."""
    with tempfile.TemporaryDirectory() as d:
        target = pathlib.Path(d, ".claude", "settings.local.json")
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({
            "model": "opus",
            "permissions": {"allow": ["Bash(cargo test:*)"],
                            "deny": ["Bash(rm:*)"]},
        }))
        granted, _held = P.build_rules(ROOT, DEVICE)
        added, kept = P._merge(target, list(granted))
        after = json.loads(target.read_text())

    assert after["model"] == "opus", "an unrelated setting was dropped"
    assert after["permissions"]["deny"] == ["Bash(rm:*)"], "a DENY was dropped"
    assert "Bash(cargo test:*)" in after["permissions"]["allow"]
    assert kept == ["Bash(cargo test:*)"]
    assert P.rule("porthole brief") in added


def test_installing_twice_adds_nothing_the_second_time():
    """It has to be re-runnable: the list grows as tools are added, and the
    way anyone will pick those up is by running it again."""
    with tempfile.TemporaryDirectory() as d:
        target = pathlib.Path(d, "settings.local.json")
        granted, _held = P.build_rules(ROOT, DEVICE)
        first, _ = P._merge(target, list(granted))
        second, kept = P._merge(target, list(granted))
    assert first, "the first install added nothing"
    assert second == [], f"the second install re-added {len(second)} rules"
    assert len(kept) == len(first)


def test_a_settings_file_that_is_not_json_is_a_refusal_not_a_rewrite():
    """The file may be hand-edited and mid-edit. Overwriting it would destroy
    work that has nothing to do with porthole."""
    from porthole_cli import Bail
    with tempfile.TemporaryDirectory() as d:
        target = pathlib.Path(d, "settings.local.json")
        target.write_text("{ not json")
        try:
            P._merge(target, ["Bash(ls:*)"])
        except Bail:
            assert target.read_text() == "{ not json", "it wrote anyway"
            return
    raise AssertionError("a broken settings file was not refused")


def test_a_rule_from_before_the_rename_is_reported_not_ignored():
    """`_merge` unions and never removes, correctly -- a settings file is the
    reader's own. So a re-run after the tk- to ph- rename grants the new path
    and leaves `Bash(tools/tk-device.sh:*)` sitting there, matching nothing.

    An ineffective allow rule looks exactly like an effective one, and the
    symptom is a permission prompt rather than an error, so it has to be said
    out loud."""
    import json
    import tempfile

    from porthole_cmd_permissions import stale_rules

    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "settings.local.json"
        assert stale_rules(target) == [], "a missing file has no stale rules"

        target.write_text(json.dumps({"permissions": {"allow": [
            "Bash(tools/tk-device.sh:*)",
            "Bash(tools/ph-device.sh:*)",
            "Bash(make test:*)",
        ]}}))
        assert stale_rules(target) == ["Bash(tools/tk-device.sh:*)"], (
            stale_rules(target))

        # Not valid JSON, and not this function's job to say so: _merge
        # reports that, with the reason.
        target.write_text("{not json")
        assert stale_rules(target) == []


if __name__ == "__main__":
    sys.exit(run(globals()))
