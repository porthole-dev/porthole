#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Attribution trailers: which lines are banned, and who has signed off.

AGENTS.md section 5 and AI.md state the convention. This is the half of it
that runs:

    REQUIRED ON A PULL REQUEST FROM A FORK
      Signed-off-by: <author>    the author's Developer Certificate of Origin,
                                 added by that human, never by an assistant
    OPTIONAL, AND ACCEPTED
      Assisted-by: / Generated-by:  disclosure, expected when an AI helped.
                                 Never required: a commit written without AI,
                                 with only its sign-off, passes every check.
    BANNED, ON EVERY SURFACE (wrong attribution)
      Co-Authored-By / Co-developed-by naming an AI (both are human-only tags
      in the kernel and in Mesa, and GitHub renders them as a co-author);
      a Signed-off-by naming an AI (only a person can certify the DCO);
      Claude-Session: lines and bare claude.ai session URLs (private links);
      "Generated with [Claude Code]" lines and robot-emoji lines.

The surfaces, and what reads each one:

    .githooks/commit-msg        a commit message, before it is written: REJECT
    `make trailers` / CI        every message in the log, and the pull request
                                body; on a pull request, also the DCO check
    issue-trailers workflow     an issue body: strip the banned lines, edit it

Hooks never add or strip anything from a commit message. They only reject a
banned line, so what lands in the log is what a person typed.

THE DCO CHECK, AND WHY IT ONLY RUNS ON A FORK
    On a pull request from a FORK every non-merge commit in base..head must
    carry a `Signed-off-by:` with its author's address -- the same check the
    kernel's and most DCO bots make. That is the case the DCO was designed
    for: an outside contributor certifying they may give us the code.

    It does NOT run on a pull request from a branch of this repository, and
    that is a deliberate retirement rather than an oversight. Only someone
    with write access can open one, every such branch is our own work, and
    the certificate was being demanded of us, by us, about our own commits.
    An assistant correctly never signs off, so every agent pull request was
    red from the moment it opened and stayed red until a human ran a tool
    whose only job was to add the missing line. A gate that is always red and
    always cleared the same way teaches people to clear it without reading,
    which is worse than no gate: it spends the attention a real check needs.

    What certifies our own work is a maintainer reading the diff and
    merging, recorded by GitHub. docs/CONTRIBUTING.md states that policy.

    Patches we send UPSTREAM are a different gate and still need a real
    sign-off from their human author at submission time. `porthole aports`
    enforces that one, and it is untouched here.

THIS FILE NEVER SCANS THE TRACKED TREE
    lib/porthole_secrets.py does. A banned line is only a problem where it is
    published as authored text, and this repo legitimately quotes every banned
    string while documenting the ban (AGENTS.md, AI.md, the templates, this
    file). A tree scan would need exemptions, and every exemption is a hole.

EVERY RULE CARRIES BOTH CONTROLS
    brain/laws/every-test-needs-a-positive-control.md. `control` must match
    and `allowed` must not; tests/test_trailers.py asserts both directions.
    `allowed` is not decoration: "generated with qca-swiss-army-knife" is a real
    string in lib/porthole_cmd_blobs.py, and `Assisted-by: Claude` is the line
    the convention asks for.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys


class Rule:
    def __init__(self, name, pattern, why, control, allowed):
        self.name = name
        self.re = re.compile(pattern)
        self.why = why
        self.control = control      # MUST match -- the line this is for
        self.allowed = allowed      # MUST NOT match -- what it must not eat


# Case-insensitive except "AI" itself, so a person named Ai is not an assistant.
_AI = r"(?:claude|anthropic|openai|chatgpt|copilot|gemini|\bgpt|\bllm\b|(?-i:\bAI\b))"

RULES = [
    # Anchored at line start, because that is what a trailer is. Prose that
    # discusses the ban mid-sentence must survive stating it.
    Rule("ai-co-author",
         r"(?im)^[ \t]*co-(?:authored|developed)-by[ \t]*:.*" + _AI,
         "Co-authored-by and Co-developed-by are human-only tags; an AI is "
         "credited with Assisted-by instead",
         control="Co-Authored-By: Claude Opus 5 (1M context) "
                 "<noreply@anthropic.com>",
         allowed="Co-authored-by: Alice Person <alice@example.com>"),

    Rule("ai-sign-off",
         r"(?im)^[ \t]*signed-off-by[ \t]*:.*" + _AI,
         "a sign-off is the Developer Certificate of Origin, which only a "
         "person can give; an assistant is credited with Assisted-by",
         control="Signed-off-by: Claude <noreply@anthropic.com>",
         allowed="Signed-off-by: Ai Nakamura <ai@example.com>"),

    Rule("session-trailer",
         r"(?im)^[ \t]*claude-session[ \t]*:",
         "a session trailer is a private link, dead for every reader",
         control="Claude-Session: https://claude.ai/code/session_01EXAMPLE",
         allowed="Assisted-by: Claude"),

    Rule("generated-with",
         r"(?i)generated with\b.{0,24}?" + _AI,
         "the 'generated with' line an AI tool appends to a commit or pull "
         "request body",
         control="\U0001f916 Generated with [Claude Code]"
                 "(https://claude.com/claude-code)",
         allowed="usually generated with qca-swiss-army-knife, not vendor.img"),

    Rule("session-url",
         r"(?i)https?://claude\.ai/code/session_\w+",
         "a session URL is a bare link to a private transcript",
         control="https://claude.ai/code/session_01EXAMPLEEXAMPLEEXAMPLE1",
         allowed="the product page, https://claude.ai/code, names no session"),

    Rule("claude-code-link",
         r"(?i)https?://claude\.com/claude-code",
         "the link target of the 'generated with' line, which also arrives "
         "on its own once the visible text is removed by hand",
         control="[Claude Code](https://claude.com/claude-code)",
         allowed="anthropic.com and claude.ai are fine to cite in prose"),

    Rule("assistant-noreply",
         r"(?i)noreply@anthropic\.com",
         "the address an AI co-author trailer carries; it survives someone "
         "renaming the trailer key",
         control="Co-authored-by: Claude <noreply@anthropic.com>",
         allowed="report a vulnerability to security@anthropic.com"),

    Rule("robot-line",
         "\U0001f916",
         "the robot emoji only ever arrives as part of a generated-with line",
         control="\U0001f916 Generated with Claude Code",
         allowed="Assisted-by: Claude"),
]

SIGNED_OFF = re.compile(r"(?im)^[ \t]*signed-off-by[ \t]*:.*<([^>\s]+)>[ \t]*$")


def scan(text: str, where: str = "-"):
    """Every rule against one blob. Returns [(where, line, rule, hit)]."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for rule in RULES:
            for m in rule.re.finditer(line):
                hits.append((where, n, rule, m.group(0)))
    return hits


def strip(text: str) -> str:
    """Drop every banned line, then the blank run they leave behind.

    For an issue body only. A commit message is rejected instead: the person
    committing is right there to fix it, and a hook that rewrites a message is
    how sign-offs used to vanish without anyone deciding they should.
    """
    kept = [ln for ln in text.splitlines()
            if not any(r.re.search(ln) for r in RULES)]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + ("\n" if kept else "")


# A bot has no person to certify the Developer Certificate of Origin, so a
# sign-off from one would be meaningless. Dependabot's dependency pull requests
# are therefore exempt -- from the sign-off alone; every other rule still
# applies, including the one that rejects a bot named in Signed-off-by.
DEPENDABOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"


def unsigned(commits, exempt=()):
    """The commits whose author did not sign off.

    `commits` is [(sha, author_email, body)]. `exempt` is author addresses that
    need no sign-off. Pure, so both directions are testable without a
    repository: a sign-off by someone else is not the author's certificate.
    """
    exempt = {e.lower() for e in exempt}
    out = []
    for sha, email, body in commits:
        if email.lower() in exempt:
            continue
        signers = {m.lower() for m in SIGNED_OFF.findall(body)}
        if email.lower() not in signers:
            out.append((sha, email))
    return out


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def _log(root, *rev):
    out = _git(root, "log", "--format=%H%x00%ae%x00%B%x00%x00", *rev)
    for entry in out.split("\0\0"):
        parts = entry.strip("\n").split("\0", 2)
        if len(parts) == 3 and parts[0]:
            yield parts


def scan_history(root):
    """Every commit message reachable from HEAD, not a range: the standing
    claim worth checking is "the log is clean", not "this push is". A shallow
    checkout sees fewer commits; the full-depth CI job makes the claim whole."""
    hits = []
    for sha, _, body in _log(root):
        hits += scan(body, sha[:12])
    return hits


def dco(root, rng, exempt=()):
    """unsigned() over the non-merge commits of a revision range."""
    return unsigned(_log(root, "--no-merges", rng), exempt)


def report(hits, out=None):
    # Bound at call time, not def time: a default of sys.stderr is captured at
    # import and cannot be redirected by a test.
    out = out or sys.stderr
    for where, line, rule, hit in hits:
        print(f"{where}:{line}: {rule.name}: {hit!r}\n    {rule.why}", file=out)
    if hits:
        print(f"\n{len(hits)} finding(s). AGENTS.md section 5: credit an "
              "assistant with `Assisted-by:`, never with a co-author, session or "
              "generated-with line. Remove the lines; do not reword them.",
              file=out)
    return 1 if hits else 0


def report_dco(missing, out=None):
    out = out or sys.stderr
    for sha, email in missing:
        print(f"{sha[:12]}: no Signed-off-by for its author <{email}>", file=out)
    if missing:
        print(f"\n{len(missing)} commit(s) without their author's sign-off. The "
              "author certifies the DCO before merge: "
              "`git rebase --signoff <base>`. An assistant never adds it.",
              file=out)
    return 1 if missing else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", metavar="FILE",
                    help="scan one body; 0 clean, 1 carries a banned line "
                         "(the commit-msg hook and the issue workflow)")
    ap.add_argument("--strip", metavar="FILE",
                    help="drop banned lines from a file in place (issue bodies)")
    ap.add_argument("--dco", metavar="RANGE",
                    help="check every non-merge commit in RANGE is signed off "
                         "by its author, e.g. origin/main..HEAD")
    ap.add_argument("--ci", action="store_true",
                    help="scan every reachable message and $PR_BODY; on a pull "
                         "request from a FORK also the DCO check over "
                         "$PR_BASE..$PR_HEAD")
    args = ap.parse_args(argv)

    if args.scan:
        # An exit code the caller branches on. The issue workflow asks this
        # before it edits, because "the file changed" is not the same question:
        # strip() normalises line endings and GitHub hands out CRLF.
        p = pathlib.Path(args.scan)
        return report(scan(p.read_text(errors="replace"), args.scan))

    if args.strip:
        p = pathlib.Path(args.strip)
        p.write_text(strip(p.read_text(errors="replace")), encoding="utf-8")
        return 0

    try:
        root = pathlib.Path(_git(pathlib.Path.cwd(), "rev-parse",
                                 "--show-toplevel").strip())
    except (subprocess.CalledProcessError, OSError):
        root = pathlib.Path(__file__).resolve().parent.parent

    if args.dco:
        try:
            return report_dco(dco(root, args.dco))
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"cannot read {args.dco}: {exc}", file=sys.stderr)
            return 69                  # EX_UNAVAILABLE, never 0

    hits, rc = [], 0
    body = os.environ.get("PR_BODY")
    is_pr = args.ci and os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
    # Set from `github.event.pull_request.head.repo.fork`, which GitHub
    # computes -- not from anything the branch or the commits can say. Absent
    # means "not a fork": a workflow that forgot to pass it gets the lenient
    # answer on purpose, because the strict one would fail every pull request
    # in the repository over a missing variable, which is the outage this
    # check is being retired for causing.
    is_fork = os.environ.get("PR_FORK") == "true"
    if body:
        hits += scan(body, "pull request body")
    elif is_pr:
        # An empty body is legitimate; a body the workflow forgot to pass is
        # not, and the two look identical from here. Say which this was.
        print("note: PR_BODY is unset or empty -- the body was not scanned",
              file=sys.stderr)
    try:
        hits += scan_history(root)
        if is_pr and is_fork:
            base, head = os.environ.get("PR_BASE"), os.environ.get("PR_HEAD")
            if not (base and head):
                # brain/laws/exit-codes-are-an-api.md: "could not run" is not
                # "passed". A pull request with no range is a workflow bug.
                print("PR_BASE/PR_HEAD unset on a pull request: the DCO check "
                      "cannot run", file=sys.stderr)
                return 69
            # PR_AUTHOR_ID comes from the event payload, which GitHub
            # controls. The commit's own name and address are whatever the
            # committer typed, so they can never grant this exemption.
            exempt = ((DEPENDABOT_EMAIL,)
                      if os.environ.get("PR_AUTHOR_ID") == "49699333" else ())
            rc = report_dco(dco(root, f"{base}..{head}", exempt))
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"cannot read the commit log: {exc}", file=sys.stderr)
        return 69                      # EX_UNAVAILABLE, never 0
    return report(hits) or rc


if __name__ == "__main__":
    sys.exit(main())
