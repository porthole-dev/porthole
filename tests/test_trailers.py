#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The trailer ban, checked in CI instead of only in an opt-in hook.

WHAT THIS EXISTS FOR
    `no-trailers` was the last MUST in lib/porthole_rules.py enforced by a
    local hook alone, and tests/test_rules.py named it in HOOK_ONLY as a known
    hole. It was not theoretical. The hook did its job -- it stripped
    `Claude-Session:` out of the commit message of #52 -- and the same lines
    were published anyway, in the pull request bodies of #51 and #52, a surface
    no check had ever read.

    So the fix is two things, and this file is the second: one pattern list
    (lib/porthole_trailers.py) instead of a copy in the hook, and a check that
    runs whatever anyone's local `core.hooksPath` says.

WHY IT ALSO SCANS THE PULL REQUEST TEMPLATES
    A template becomes the initial body of a pull request, and both templates
    state the trailer ban by naming the trailers. If the patterns matched that,
    CI would fail on every pull request that used the template, and the check
    would be turned off within a day. Asserting the prose survives is what lets
    the patterns stay strict.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_trailers as T                               # noqa: E402

# Verbatim, from the harness defaults that produced #51 and #52. Analysing what
# actually arrives is the point: the commit form and the pull request form are
# different text, and only the commit form was ever in the hook.
OBSERVED = [
    "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>",
    "Claude-Session: https://claude.ai/code/session_01EXAMPLEEXAMPLEEXAMPLE1",
    "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)",
    "https://claude.ai/code/session_01EXAMPLEEXAMPLEEXAMPLE2",
    # Older shapes the two rewrites removed, kept so a revert is caught too.
    "Signed-off-by: Someone <someone@example.com>",
    "Co-authored-by: Claude <noreply@anthropic.com>",
    "\U0001f916 Generated with Claude Code",
]


def test_every_rule_matches_its_control():
    """A scanner that matches nothing passes silently -- the exact failure mode
    it exists to prevent. brain/laws/every-test-needs-a-positive-control.md."""
    dead = [r.name for r in T.RULES if not r.re.search(r.control)]
    assert not dead, f"these rules no longer match their own control: {dead}"


def test_no_rule_matches_what_it_must_not_eat():
    """The other direction. `generated with qca-swiss-army-knife` is a real
    string in lib/porthole_cmd_blobs.py, and a lazier pattern eats it."""
    greedy = [(r.name, r.allowed) for r in T.RULES if r.re.search(r.allowed)]
    assert not greedy, f"these rules match prose they must not: {greedy}"


def test_every_observed_trailer_is_caught():
    """The list above is the harness output itself, not a paraphrase of it. A
    pattern set that passes its own controls but misses what actually arrives
    is how #51 shipped."""
    missed = [line for line in OBSERVED if not T.scan(line)]
    assert not missed, ("these are published verbatim by a harness default and "
                        "nothing matches them:\n  " + "\n  ".join(missed))


def test_strip_removes_the_trailers_and_keeps_the_message():
    msg = ("fix: the thing\n\nWhy it broke, in a sentence.\n\n"
           + "\n".join(OBSERVED[:4]) + "\n")
    out = T.strip(msg)
    assert not T.scan(out), f"strip left a trailer behind:\n{out}"
    assert "Why it broke, in a sentence." in out, "strip ate the message body"
    assert out.endswith("sentence.\n"), (
        f"strip left the blank run the trailers sat in:\n{out!r}")


def test_the_hook_holds_no_second_copy_of_the_patterns():
    """The copy IS the defect. The hook carried sed expressions for the commit
    surface while the body surface had none, so the two could not be fixed at
    once -- and were not."""
    hook = (ROOT / ".githooks/commit-msg").read_text()
    assert "porthole_trailers.py" in hook, (
        "the commit-msg hook no longer calls the shared scanner")
    assert "Co-Authored-By:/Id" not in hook and "sed -E" not in hook, (
        "the hook has grown its own pattern list again")


def test_the_pull_request_templates_survive_stating_the_ban():
    """A template is published as a pull request body. If stating the rule
    trips the check, the check gets disabled instead of the rule obeyed."""
    for rel in ("PULL_REQUEST_TEMPLATE.md", "PULL_REQUEST_TEMPLATE/brain-note.md"):
        path = ROOT / ".github" / rel
        hits = T.scan(path.read_text(), rel)
        assert not hits, (f"{rel} would fail its own check:\n  "
                          + "\n  ".join(f"{h[1]}: {h[3]!r}" for h in hits))


def test_no_commit_message_in_this_history_carries_one():
    """The standing claim. The history has been rewritten twice to make it
    true; this is what keeps it true without a third.

    A shallow checkout sees fewer commits and still passes honestly -- the
    `trailers` job in ci.yml fetches full depth, which is what makes the claim
    whole. Asserting the depth here would fail the matrix jobs for no reason.
    """
    try:
        hits = T.scan_history(ROOT)
    except (subprocess.CalledProcessError, OSError):
        return                      # a git archive extraction has no log
    assert not hits, ("commit messages carry trailers:\n  "
                      + "\n  ".join(f"{w} line {n}: {h!r}"
                                    for w, n, _, h in hits))


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
