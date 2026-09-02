#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Attribution trailers, written as patterns instead of as a sentence.

AGENTS.md section 5 bans them. This is the half of it that runs, and it exists
because the other halves each covered only part of the surface:

    prose in AGENTS.md          covered everything, enforced nothing
    .githooks/commit-msg        enforced it, on commit messages, opt-in
    nothing at all              pull request bodies

The history has been rewritten twice over trailers (35 trailers, 35 session
URLs and 59 sign-offs the first time; 49, 49 and 32 the second). The third
escape did not touch a commit at all: the hook stripped `Claude-Session:` from
the message exactly as designed, and the same two lines went out in the pull
request body of #51 and #52, where no check had ever looked.

    A rule holds on the surfaces its enforcer reads. Adding a surface to the
    ban means adding it here, not to the prose.

THIS FILE NEVER SCANS THE TRACKED TREE
    lib/porthole_secrets.py does, and must -- a serial in a committed file is
    the leak. A trailer is different: the ban is on *publishing* one, and this
    repo legitimately quotes every banned string while documenting the ban.
    AGENTS.md section 5, docs/CONTRIBUTING.md, the pull request templates and
    this file all name `Co-Authored-By:` on purpose. A tree scan would have to
    exempt them, and every exemption is a hole someone later files a leak
    through. So the scanned surfaces are exactly the two that get published as
    authored text: commit messages and pull request bodies.

EVERY RULE CARRIES BOTH CONTROLS
    brain/laws/every-test-needs-a-positive-control.md. A scanner that matches
    nothing passes silently, which is the exact failure mode it exists to
    prevent -- so `control` must match and `allowed` must not, and
    tests/test_trailers.py asserts both directions for every rule. `allowed` is
    not decoration here: "generated with qca-swiss-army-knife" is a real string
    in lib/porthole_cmd_blobs.py, and a lazier `generated with` pattern eats it.
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
        self.control = control      # MUST match -- the trailer this is for
        self.allowed = allowed      # MUST NOT match -- prose it must not eat


RULES = [
    # Anchored at line start, because that is what a trailer is. Prose that
    # discusses the ban mid-sentence -- "no Co-Authored-By, no Signed-off-by"
    # -- is how AGENTS.md states the rule, and must survive stating it.
    Rule("attribution-trailer",
         r"(?im)^[ \t]*(?:co-authored-by|signed-off-by|claude-session"
         r"|assisted-by|generated-by|co-developed-by|ai-assisted-by)[ \t]*:",
         "a trailer is injected by a harness default rather than typed by "
         "anyone, and a sign-off is an assertion only a person can make",
         control="Co-Authored-By: Claude Opus 5 (1M context) "
                 "<noreply@anthropic.com>",
         allowed="the ban covers Co-Authored-By: and Signed-off-by: alike"),

    Rule("generated-with",
         r"(?i)generated with[ \t]*\[?claude",
         "the 'generated with' line the harness appends to a pull request "
         "body; #51 and #52 both shipped one",
         control="\U0001f916 Generated with [Claude Code]"
                 "(https://claude.com/claude-code)",
         allowed="usually generated with qca-swiss-army-knife, not vendor.img"),

    Rule("session-url",
         r"(?i)https?://claude\.ai/code/session_\w+",
         "a session URL is a bare link to a transcript, and it identifies the "
         "session rather than the change",
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
         "the address a Co-Authored-By trailer carries; it survives someone "
         "renaming the trailer key",
         control="Co-authored-by: Claude <noreply@anthropic.com>",
         allowed="report a vulnerability to security@anthropic.com"),
]


def scan(text: str, where: str = "-"):
    """Every rule against one blob. Returns [(where, line, rule, hit)]."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for rule in RULES:
            for m in rule.re.finditer(line):
                hits.append((where, n, rule, m.group(0)))
    return hits


def strip(text: str) -> str:
    """Drop every offending line, then the blank run they leave behind.

    Strip rather than reject, for a commit message only: the lines are a
    harness default, not an argument anyone is having, and a rejection just
    gets retried with the same body. A pull request body is not stripped --
    nothing owns it at the moment it is written, so there it is a hard failure.
    """
    kept = [ln for ln in text.splitlines()
            if not any(r.re.search(ln) for r in RULES)]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + ("\n" if kept else "")


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def scan_history(root):
    """Every commit message reachable from HEAD.

    Deliberately the whole history, not a range against a base. A range needs
    a base SHA plumbed in from the workflow, and the two rewrites mean the
    standing claim worth checking is "the log is clean", not "this push is".
    Where the checkout is shallow this sees fewer commits and says so; it is
    the depth-0 job in ci.yml that makes the claim whole.
    """
    hits = []
    out = _git(root, "log", "--format=%H%x00%B%x00%x00")
    for entry in out.split("\0\0"):
        sha, _, body = entry.strip("\n").partition("\0")
        if sha:
            hits += scan(body, sha[:12])
    return hits


def report(hits, out=sys.stderr):
    for where, line, rule, hit in hits:
        print(f"{where}:{line}: {rule.name}: {hit!r}\n    {rule.why}", file=out)
    if hits:
        print(f"\n{len(hits)} finding(s). AGENTS.md section 5: no trailers and "
              "no signatures of any kind, on a commit or a pull request body. "
              "Remove the lines; do not reword them.", file=out)
    return 1 if hits else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--strip", metavar="FILE",
                    help="rewrite a commit message file in place")
    ap.add_argument("--ci", action="store_true",
                    help="scan every reachable commit message, and $PR_BODY "
                         "when the workflow set it")
    args = ap.parse_args(argv)

    if args.strip:
        p = pathlib.Path(args.strip)
        p.write_text(strip(p.read_text(errors="replace")), encoding="utf-8")
        return 0

    try:
        root = pathlib.Path(_git(pathlib.Path.cwd(), "rev-parse",
                                 "--show-toplevel").strip())
    except (subprocess.CalledProcessError, OSError):
        root = pathlib.Path(__file__).resolve().parent.parent

    hits = []
    body = os.environ.get("PR_BODY")
    if body:
        hits += scan(body, "pull request body")
    elif args.ci and os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        # An empty body is legitimate; a body the workflow forgot to pass is
        # not, and the two look identical from here. Say which this was.
        print("note: PR_BODY is unset or empty -- the body was not scanned",
              file=sys.stderr)
    try:
        hits += scan_history(root)
    except (subprocess.CalledProcessError, OSError) as exc:
        # brain/laws/exit-codes-are-an-api.md: "could not run" is not "passed".
        print(f"cannot read the commit log: {exc}", file=sys.stderr)
        return 69                      # EX_UNAVAILABLE, never 0
    return report(hits)


if __name__ == "__main__":
    sys.exit(main())
