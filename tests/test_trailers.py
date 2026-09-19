#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The attribution convention, checked in CI instead of only in an opt-in hook.

WHAT THIS EXISTS FOR
    AGENTS.md section 5: an assistant is credited with `Assisted-by:`, the
    human certifies with `Signed-off-by:`, and the harness lines (an AI
    co-author, a session trailer or URL, a generated-with line) are banned on
    every surface. This file is the contract for lib/porthole_trailers.py.

    The ban has escaped before on a surface no check read: the hook held the
    commit message of #52 and the same lines went out in the pull request
    bodies of #51 and #52, then in issue #54. So there is one pattern list,
    and a check that runs whatever anyone's local `core.hooksPath` says.

WHY IT ALSO SCANS THE PULL REQUEST TEMPLATES
    A template becomes the initial body of a pull request, and both templates
    state the ban by naming the lines. If the patterns matched that, CI would
    fail on every pull request that used the template.
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
    "Co-authored-by: Claude <noreply@anthropic.com>",
    "Co-developed-by: Claude <claude@example.com>",
    "\U0001f916 Generated with Claude Code",
    "Signed-off-by: Claude <noreply@anthropic.com>",
    "Signed-off-by: GitHub Copilot <copilot@example.com>",
    # Not verbatim: the same boilerplate from other tools must not slip past a
    # pattern written for one vendor.
    "Generated with [ChatGPT](https://chatgpt.com)",
    "Generated with GitHub Copilot",
]

# The convention itself. A pattern set that eats these rejects every commit
# the convention asks for, and gets switched off within a day.
KEPT = [
    "Assisted-by: Claude",
    "Assisted-by: LLM",
    "Generated-by: Claude (Claude Opus 5)",
    "Signed-off-by: Ai Nakamura <ai@example.com>",
    "Signed-off-by: Someone <someone@example.com>",
    "Co-authored-by: Alice Person <alice@example.com>",
    "Reported-by: Bob <bob@example.com>",
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


def test_every_observed_harness_line_is_caught():
    """The list above is the harness output itself, not a paraphrase of it."""
    missed = [line for line in OBSERVED if not T.scan(line)]
    assert not missed, ("these are published verbatim by a harness default and "
                        "nothing matches them:\n  " + "\n  ".join(missed))


def test_the_convention_lines_are_never_banned():
    """Assisted-by and Signed-off-by are what the convention asks for, and a
    human co-author is a human's business."""
    eaten = [line for line in KEPT if T.scan(line)]
    assert not eaten, "the convention's own lines are banned:\n  " + "\n  ".join(eaten)


def test_strip_removes_banned_lines_and_keeps_the_trailers():
    msg = ("fix: the thing\n\nWhy it broke, in a sentence.\n\n"
           + "\n".join(OBSERVED[:4]) + "\n\nAssisted-by: Claude\n"
           "Signed-off-by: Someone <someone@example.com>\n")
    out = T.strip(msg)
    assert not T.scan(out), f"strip left a banned line behind:\n{out}"
    assert "Why it broke, in a sentence." in out, "strip ate the message body"
    assert out.endswith("Assisted-by: Claude\n"
                        "Signed-off-by: Someone <someone@example.com>\n"), (
        f"strip ate the convention's trailers:\n{out!r}")


def test_the_hook_rejects_and_never_rewrites():
    """The hook used to strip, and a stripping hook is how a sign-off vanished
    without anyone deciding it should. It reads the shared list and exits
    non-zero; it never writes the message file."""
    hook = (ROOT / ".githooks/commit-msg").read_text()
    assert "porthole_trailers.py\" --scan" in hook, (
        "the commit-msg hook no longer asks the shared scanner")
    assert "--strip" not in hook, "the commit-msg hook rewrites messages again"
    assert "sed -E" not in hook, "the hook has grown its own pattern list"


def test_the_pull_request_templates_survive_stating_the_ban():
    """A template is published as a pull request body. If stating the rule
    trips the check, the check gets disabled instead of the rule obeyed."""
    for rel in ("PULL_REQUEST_TEMPLATE.md", "PULL_REQUEST_TEMPLATE/brain-note.md"):
        path = ROOT / ".github" / rel
        hits = T.scan(path.read_text(), rel)
        assert not hits, (f"{rel} would fail its own check:\n  "
                          + "\n  ".join(f"{h[1]}: {h[3]!r}" for h in hits))


def test_the_issue_body_surface_has_an_enforcer():
    """#54 was filed carrying two banned lines while the hook and the pull
    request check both held. The third surface gets a reader too -- and it
    edits the issue rather than going red on an event nobody watches."""
    flow = (ROOT / ".github/workflows/issue-trailers.yml").read_text()
    assert "types: [opened, edited]" in flow, (
        "without `edited` a body is checked once at open and a line pasted "
        "in afterwards stays")
    assert "issues: write" in flow, "the workflow cannot fix what it finds"
    assert "make issue-trailers" in flow, (
        "the workflow has grown its own copy of the steps")
    assert "${{ github.event.issue.body }}" not in flow.split("run:")[-1], (
        "the body must reach the shell through the environment, never through "
        "the command line: it is attacker-controlled text")


def test_scan_exits_nonzero_only_on_a_body_that_carries_one():
    """The exit code the hook and the workflow branch on, both directions.
    Comparing a stripped file against the original instead would edit forever:
    strip() normalises line endings and GitHub hands out CRLF."""
    import contextlib
    import io
    import tempfile
    noise = contextlib.redirect_stderr(io.StringIO())   # the finding it prints
    with tempfile.TemporaryDirectory() as d, noise:
        clean = pathlib.Path(d, "clean.md")
        clean.write_text("## What happened\r\n\r\nIt broke.\r\n\r\n"
                         "Assisted-by: Claude\r\n")
        assert T.main(["--scan", str(clean)]) == 0
        dirty = pathlib.Path(d, "dirty.md")
        dirty.write_text("It broke.\n\n" + OBSERVED[2] + "\n")
        assert T.main(["--scan", str(dirty)]) == 1


def test_dco_wants_the_authors_own_sign_off():
    """Both directions, and the case a lazy check passes: a sign-off by someone
    other than the author is not the author's certificate."""
    signed = ("a1", "me@example.com",
              "x: y\n\nWhy.\n\nAssisted-by: Claude\n"
              "Signed-off-by: Me Person <me@example.com>\n")
    unsigned = ("b2", "me@example.com", "x: y\n\nWhy.\n\nAssisted-by: Claude\n")
    by_other = ("c3", "me@example.com",
                "x: y\n\nSigned-off-by: Someone Else <else@example.com>\n")
    case = ("d4", "Me@Example.com",
            "x: y\n\nSigned-off-by: Me Person <me@example.COM>\n")
    assert T.unsigned([signed, case]) == []
    assert T.unsigned([unsigned, by_other]) == [("b2", "me@example.com"),
                                                ("c3", "me@example.com")]


def test_a_human_only_commit_passes_with_just_its_sign_off():
    """Assisted-by is disclosure, never a requirement: a contribution written
    without AI, carrying nothing but its author's sign-off, passes both the
    banned-line scan and the DCO check."""
    body = ("fix: the thing\n\nWhy it broke.\n\n"
            "Signed-off-by: Alice Person <alice@example.com>\n")
    assert T.scan(body) == []
    assert T.unsigned([("a1", "alice@example.com", body)]) == []


def test_an_ai_assisted_commit_passes_with_disclosure_and_sign_off():
    body = ("fix: the thing\n\nWhy it broke.\n\nAssisted-by: Claude\n"
            "Signed-off-by: Alice Person <alice@example.com>\n")
    assert T.scan(body) == []
    assert T.unsigned([("a1", "alice@example.com", body)]) == []


def test_dco_reads_a_real_range_and_skips_merges():
    """The range half, on a real repository: base..head, no merge commits (a
    pull request's merge commit is GitHub's, and nobody signs it)."""
    import os
    import tempfile
    env = dict(os.environ, GIT_AUTHOR_NAME="Me", GIT_AUTHOR_EMAIL="me@example.com",
               GIT_COMMITTER_NAME="Me", GIT_COMMITTER_EMAIL="me@example.com",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    with tempfile.TemporaryDirectory() as d:
        def git(*args):
            return subprocess.run(["git", "-C", d, *args], env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        git("commit", "-q", "--allow-empty", "-m", "base")
        base = git("rev-parse", "HEAD")
        trunk = git("symbolic-ref", "--short", "HEAD")
        git("checkout", "-q", "-b", "topic")
        git("commit", "-q", "--allow-empty", "-m", "signed", "-s")
        git("commit", "-q", "--allow-empty", "-m", "unsigned\n\nAssisted-by: Claude")
        git("checkout", "-q", trunk)
        git("commit", "-q", "--allow-empty", "-m", "moved on", "-s")
        git("merge", "-q", "--no-ff", "--no-edit", "topic")
        missing = T.dco(pathlib.Path(d), f"{base}..HEAD")
    assert [email for _, email in missing] == ["me@example.com"], (
        f"expected exactly the one unsigned commit, got {missing}: the merge "
        "commit or a signed commit was counted")


def test_ci_passes_the_dco_range_through_the_environment():
    """The range reaches the check the way the body does: through env, never
    substituted into a run: line."""
    flow = (ROOT / ".github/workflows/ci.yml").read_text()
    job = flow.split("  trailers:")[1]
    assert "fetch-depth: 0" in job, "the DCO range needs the base commit"
    for var in ("PR_BASE: ${{ github.event.pull_request.base.sha }}",
                "PR_HEAD: ${{ github.event.pull_request.head.sha }}"):
        assert var in job, f"the trailers job does not export {var.split(':')[0]}"
    assert "run: make trailers" in job


def test_no_commit_message_in_this_history_carries_a_banned_line():
    """The standing claim. A shallow checkout sees fewer commits and still
    passes honestly -- the `trailers` job in ci.yml fetches full depth, which
    is what makes the claim whole."""
    try:
        hits = T.scan_history(ROOT)
    except (subprocess.CalledProcessError, OSError):
        return                      # a git archive extraction has no log
    assert not hits, ("commit messages carry banned lines:\n  "
                      + "\n  ".join(f"{w} line {n}: {h!r}"
                                    for w, n, _, h in hits))




def test_a_bot_is_exempt_from_the_dco_only_when_named():
    """A bot has no person to certify the DCO, so Dependabot is exempt -- but
    only when the caller passes the exemption, which the workflow does solely
    on the event's numeric user id. The commit's own author fields never earn
    it, or any fork pull request could claim the exemption."""
    bot = ("a1", T.DEPENDABOT_EMAIL, "ci: bump the actions group")
    human = ("b2", "me@example.com", "fix: a thing")

    assert T.unsigned([bot]) == [("a1", T.DEPENDABOT_EMAIL)]
    assert T.unsigned([bot], (T.DEPENDABOT_EMAIL,)) == []
    assert T.unsigned([human], (T.DEPENDABOT_EMAIL,)) == [("b2", "me@example.com")]
    # The exemption is for the sign-off, not for attribution: a bot named in a
    # Signed-off-by is still a finding of the scanner, which is a separate rule.
    assert T.unsigned([bot], ("someone@else.org",)) == [("a1", T.DEPENDABOT_EMAIL)]


def test_the_ci_path_actually_exempts_dependabot():
    """The --ci path, not just unsigned(): the exemption has to reach dco()
    with the right argument. A unit test of unsigned() passed while the call
    site handed `exempt` to report_dco(), whose second argument is the output
    stream -- which only showed up as an AttributeError on a live run."""
    import os
    import tempfile
    bot = "49699333+dependabot[bot]@users.noreply.github.com"
    env = dict(os.environ, GIT_AUTHOR_NAME="dependabot[bot]", GIT_AUTHOR_EMAIL=bot,
               GIT_COMMITTER_NAME="dependabot[bot]", GIT_COMMITTER_EMAIL=bot,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    with tempfile.TemporaryDirectory() as d:
        def git(*args):
            return subprocess.run(["git", "-C", d, *args], env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        git("commit", "-q", "--allow-empty", "-m", "base")
        base = git("rev-parse", "HEAD")
        git("commit", "-q", "--allow-empty", "-m", "ci: bump the actions group")
        head = git("rev-parse", "HEAD")

        def run(author_id, fork="true"):
            e = dict(env, PR_BODY="a body", PR_BASE=base, PR_HEAD=head)
            e["PR_AUTHOR_ID"] = author_id
            e["PR_FORK"] = fork
            e["GITHUB_EVENT_NAME"] = "pull_request"
            return subprocess.run(
                [sys.executable, str(ROOT / "lib/porthole_trailers.py"), "--ci"],
                cwd=d, env=e, capture_output=True, text=True)

        exempt = run("49699333")
        assert exempt.returncode == 0, (
            "Dependabot was not exempted on the CI path: "
            f"rc={exempt.returncode}\n{exempt.stdout}\n{exempt.stderr}")
        assert "Traceback" not in exempt.stderr, exempt.stderr

        human = run("1234")
        assert human.returncode != 0, (
            "a non-Dependabot author was exempted from the sign-off")

        # The retirement, with its positive control right above it: the SAME
        # unsigned commit that fails from a fork passes from a branch of this
        # repository. Asserting only the pass would also pass if the whole
        # check had stopped working.
        ours = run("1234", fork="false")
        assert ours.returncode == 0, (
            "an unsigned commit on our own branch was failed by the DCO check, "
            f"which no longer applies to it:\n{ours.stdout}\n{ours.stderr}")
        # Absent, not just false: a workflow that forgets the variable must get
        # the lenient answer, never fail every pull request in the repository.
        missing = run("1234", fork="")
        assert missing.returncode == 0, (
            "a missing PR_FORK was treated as a fork: "
            f"{missing.stdout}\n{missing.stderr}")


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
