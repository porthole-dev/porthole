#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole brief --compact`: the agent entry point, and its byte budget.

WHY THERE IS A BUDGET AT ALL
    Measured 2026-09-18 on a configured host, `brief --no-device --json`:
    61228 bytes, of which `findings` was 40093 (65%) and `rules` 9716 (16%).
    Four fifths of what AGENTS.md tells every session to read first was a
    catalogue -- and a catalogue is the one thing a search is strictly better
    at, because you search for the theory you are about to pursue and the
    `refutes:` line finds the note that already killed it.

WHY THE BUDGET IS NOT THE POINT
    Bytes are a tokenizer-independent proxy, not a token count, and a smaller
    prompt is not by itself evidence that an agent stopped reading the source.
    The budget exists to stop the catalogue creeping back in. The assertions
    that matter are the ones below it: every DEVICE fact survives compaction.
    A brief that hit the budget by dropping a trap would pass a byte check and
    be worse than the thing it replaced.

`_compact` is pure -- a payload in, a payload out -- so all of this runs with
no device, no subprocess and no configured host.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "lib"))

import _runner                                              # noqa: E402
import porthole_cmd_brief as brief                          # noqa: E402

# The budget, in bytes of serialized JSON. Deliberately above the 7083 measured
# when this was written: a budget with no headroom fails on the next legitimate
# device fact, and a check that fires on a healthy tree gets muted.
BUDGET = 8192


def _payload():
    """A brief-shaped payload with the two bulk fields realistically large.

    Built here rather than by running the verb, because the verb needs a
    configured host and this is a property of `_compact`, not of discovery.
    """
    return {
        "porthole_version": "0.1.0",
        "root": "/somewhere/porthole",
        "device": {
            "codename": "google-taimen", "name": "Pixel 2 XL",
            "soc": "msm8998", "state": "BOOTED", "kernel": "6.18.0",
            "ssh_target": "<user>@<ipv4>", "profile_gaps": [],
            "traps": ["a gpu page fault wedges a540 unrecoverably",
                      "suspend hard-hangs the SoC via ipa"],
        },
        "workspace": {"up": True, "work dir": "/w"},
        "hooks": {"installed": True},
        "config_drift": [],
        "tools": {"count": 157, "undocumented": [],
                  "catalogue": "porthole tools --json"},
        "brain": {
            "laws": [{"law": f"law number {i}", "id": f"law-{i}"}
                     for i in range(10)],
            "search": "porthole brain <query> [--scope <scope>]",
            "index": "brain/INDEX.md",
            "contribute": {"duty": "write it down", "new": "porthole brain new",
                           "lint": "porthole brain lint",
                           "submit": "porthole brain submit", "bar": "one idea"},
        },
        "rules": [{"id": f"rule-{i}", "level": "MUST" if i % 2 else "SHOULD",
                   "rule": f"statement {i}", "why": "why " * 40,
                   "enforced_by": ["tests/test_x.py", "hooks/y"]}
                  for i in range(14)],
        "laws": [{"law": f"law number {i}", "id": f"law-{i}"} for i in range(10)],
        "findings": [{"finding": f"finding number {i}", "id": f"f-{i}",
                      "refutes": "a theory " * 20} for i in range(210)],
        "entrypoints": {"agents": "AGENTS.md", "humans": "README.md"},
        "next": ["do the thing"],
        "port": {"milestone": "audio", "next": {"cmd": "porthole build"}},
        "aports": {"carried": 3},
        "compact": False,
    }


def test_the_compact_brief_fits_the_budget():
    """The positive control is the FULL payload in the same assertion: if
    _compact silently became the identity function, the budget alone would
    still catch it only by luck. Asserting the full one is over budget proves
    the fixture is big enough for the test to mean anything."""
    full = json.dumps(_payload())
    assert len(full) > BUDGET, (
        "the fixture is too small to prove anything about compaction",
        len(full))
    small = json.dumps(brief._compact(_payload()))
    assert len(small) <= BUDGET, (len(small), BUDGET)


def test_compaction_drops_catalogue_and_nothing_else():
    """What must survive: every fact about THIS device and THIS moment. A
    lookup can replace a catalogue; it cannot replace the state the session is
    wrong without."""
    out = brief._compact(_payload())
    src = _payload()
    for key in ("device", "workspace", "hooks", "config_drift", "port",
                "aports", "next", "porthole_version", "root"):
        assert out[key] == src[key], key


def test_a_device_trap_is_never_compacted_away():
    """The one that would make a byte budget actively harmful. Traps are the
    encoded reason a device bricks, and they are small -- there is never a
    reason to trade one for bytes."""
    out = brief._compact(_payload())
    assert out["device"]["traps"] == _payload()["device"]["traps"]
    assert len(out["device"]["traps"]) == 2, out["device"]["traps"]


def test_the_findings_catalogue_becomes_the_search_that_replaces_it():
    """Dropping 210 findings is only defensible if what replaces them is the
    better way to reach the same notes. The count proves nothing was hidden;
    the search string is the instruction that makes it work."""
    out = brief._compact(_payload())
    assert out["findings"]["count"] == 210, out["findings"]
    assert "porthole brain" in out["findings"]["search"], out["findings"]
    assert "refutes" in out["findings"]["why"], out["findings"]


def test_every_must_rule_survives_and_the_reference_is_named():
    """A MUST is an instruction and stays. `why`/`enforced_by` are reference
    and go, so the command that brings them back has to be in the payload --
    otherwise this is information loss rather than deferral."""
    src, out = _payload(), brief._compact(_payload())
    musts = [r["id"] for r in src["rules"] if r["level"] == "MUST"]
    assert [r["id"] for r in out["rules"]] == musts, out["rules"]
    assert musts, "the fixture has no MUST rules; the assertion is vacuous"
    assert all("why" not in r for r in out["rules"]), out["rules"]
    assert "porthole brief --json" in out["rules_full"], out["rules_full"]


def test_compact_is_flagged_in_the_payload():
    """A consumer must be able to tell a compact brief from a full one without
    measuring it -- otherwise a missing `findings` list reads as "this port has
    no findings", which is the opposite of true."""
    assert brief._compact(_payload())["compact"] is True
    assert _payload()["compact"] is False


def test_the_verb_offers_compact():
    flags = [names[0] for names, _kw in brief.SPEC["args"]]
    assert "--compact" in flags, flags


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
