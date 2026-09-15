#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The manifest, checked: every MUST has a live enforcer, and no copy has drifted.

This is what converts governance from something read into something run. It
makes "a MUST with no enforcer" a build failure, which is precisely the defect
that let a device serial reach a public branch: "no personal information" was a
MUST in the PR checklist, its enforcer scanned tools() only, and nothing
anywhere said the two did not match.

WHAT IT REFUSES TO LET HAPPEN AGAIN
    The agent rules lived in four places -- AGENTS.md, the bring-up skill,
    brain/workflow/agent-protocol.md and lib/porthole_cmd_brief.py -- and the
    four disagreed. brief's copy, the one an agent actually executes, was
    missing "never ask for host root" and "never hardcode a value". So the
    rules an agent followed depended on which file it happened to open.

Needs no device and no network. It does run `porthole brief --no-device --json`,
because the question "does an agent actually receive this rule" cannot be
answered by reading the source that is supposed to send it.
"""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_rules as rules                              # noqa: E402

# A hook is opt-in: git ignores .githooks until someone runs
# `git config core.hooksPath .githooks`, and nothing checks that they did. So a
# hook is not CI enforcement, and a MUST resting on one alone is a known hole.
# This set is the honest record of which. Shrinking it is the work; GROWING it
# without deciding to is what this test exists to stop.
#
# It is empty, and that took a leak to earn. The trailer rule (then
# `no-trailers`, now `attribution-trailers`) sat here alone,
# annotated as a known hole, and the hole opened exactly as described: the hook
# stripped the trailer from the commit message of #52 and the same lines went
# out in the pull request body, which no hook and no test had ever read.
# tests/test_trailers.py and the `trailers` job in ci.yml close it on both
# surfaces. Do not put a rule back in here without closing it the same way.
HOOK_ONLY = set()

ID = re.compile(r"^[a-z][a-z0-9-]*$")


def _brief():
    out = subprocess.run(
        [sys.executable, str(ROOT / "bin/porthole"), "brief", "--no-device",
         "--json"], capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, f"brief failed:\n{out.stderr}"
    return json.loads(out.stdout)


def _ci_reached(enforcer):
    """A test file is reached by `make ci`: tests/run-suites.sh globs
    tests/test_*.py, `make test` runs it, and `make ci` runs `make test`.
    tests/test_tools.py already pins the make/CI half of that chain."""
    return enforcer.split("::")[0].startswith("tests/test_")


def test_the_glob_this_file_reasons_about_still_exists():
    """_ci_reached infers "it runs in CI" from run-suites.sh globbing. If that
    ever becomes a hand-kept list, the inference is wrong and every MUST below
    would be certified by an assumption instead of a fact."""
    assert "tests/test_*.py" in (ROOT / "tests/run-suites.sh").read_text(), (
        "run-suites.sh no longer globs; _ci_reached() is now lying")


def test_rule_ids_are_unique_and_kebab_case():
    ids = [r.id for r in rules.RULES]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate rule ids: {sorted(dupes)}"
    bad = [i for i in ids if not ID.match(i)]
    assert not bad, f"ids are kebab-case: {bad}"


def test_every_must_names_an_enforcer():
    """The whole point. A MUST is machine-checkable and enforced; if nothing
    can check it, it is a SHOULD however strongly anyone feels. Once one MUST
    is decorative, they all read as decorative."""
    naked = [r.id for r in rules.must() if not r.enforced_by]
    assert not naked, (
        "these are MUST with nothing enforcing them. Either name an enforcer "
        "or make them SHOULD:\n  " + "\n  ".join(naked))


def test_every_named_enforcer_exists():
    """A rule pointing at a check that was renamed or deleted is worse than a
    rule with no check, because the manifest then certifies it."""
    missing = []
    for r in rules.RULES:
        for e in r.enforced_by:
            rel, _, name = e.partition("::")
            path = ROOT / rel
            if not path.exists():
                missing.append(f"{r.id}: {rel} does not exist")
            elif name and f"def {name}(" not in path.read_text():
                missing.append(f"{r.id}: {rel} has no {name}()")
    assert not missing, "\n  ".join(missing)


def test_every_must_is_enforced_in_ci_or_is_a_declared_hook_only_gap():
    """A hook only runs for someone who opted in. No MUST rests on one now;
    naming the set is what stops one quietly joining it again."""
    hook_only = {r.id for r in rules.must()
                 if not any(_ci_reached(e) for e in r.enforced_by)}
    assert hook_only == HOOK_ONLY, (
        f"the set of MUSTs that only a local hook enforces changed.\n"
        f"  newly hook-only: {sorted(hook_only - HOOK_ONLY)}\n"
        f"  now CI-enforced (remove from HOOK_ONLY): {sorted(HOOK_ONLY - hook_only)}")


def test_the_generated_blocks_are_current():
    """AGENTS.md and the skill carry blocks rendered from the manifest. This is
    what makes drift impossible rather than merely detectable -- duplication
    with drift is worse than either copy alone, and this repo has already paid
    for it: AGENTS.md and brain/workflow/commit-conventions.md still disagree
    about whether upstream sign-offs are an exception."""
    stale = []
    for rel, make in rules.GENERATED.items():
        have = rules.block_of(ROOT, rel)
        assert have is not None, f"{rel} has lost its generated-rules markers"
        if have != make():
            stale.append(rel)
    assert not stale, (
        "these are stale -- run `make rules`, and edit lib/porthole_rules.py "
        "rather than the block:\n  " + "\n  ".join(stale))


def test_brief_serves_every_rule_to_an_agent():
    """The drift that started this. brief is what section 9 tells an agent to
    run first, and its hand-written copy carried 6 of the 10 rules the other
    surfaces had. Serving the manifest is only half the fix; asserting the
    agent receives all of it is the other half."""
    served = {r["id"] for r in _brief()["rules"]}
    missing = {r.id for r in rules.RULES} - served
    assert not missing, f"brief --json does not serve: {sorted(missing)}"
    assert all(r["level"] in (rules.MUST, rules.SHOULD)
               for r in _brief()["rules"]), "a rule reached an agent unlevelled"


def test_every_law_reaches_an_agent():
    """brain/laws/ is delivered by brief reading the directory, not by being
    restated in the manifest -- restating it would recreate the drift this all
    exists to remove. What must hold is that writing a law changes what an
    agent is shown, which is exactly what failed before: brief listed three of
    ten laws and silently omitted seven, including one that cost a day."""
    on_disk = {p.stem for p in (ROOT / "brain/laws").glob("*.md")}
    served = {law["id"] for law in _brief()["laws"]}
    assert on_disk <= served, (
        "these laws exist and no agent is shown them:\n  "
        + "\n  ".join(sorted(on_disk - served)))


def test_the_login_never_reaches_json_output():
    """SECURITY.md names this class itself -- credential material leaking into
    JSON output -- and brief's is the output most likely to be pasted into a
    brain note or a PR, because section 9 tells every agent to run it first."""
    target = _brief()["device"]["ssh_target"]
    assert "@" not in target or target.startswith("<user>@"), (
        f"brief --json published a login: {target!r}")


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
