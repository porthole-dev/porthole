#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device; gh logged in)
# env: SIGNOFF_NAME SIGNOFF_EMAIL (else git config --global signoff.name / signoff.email)
# exits: 0 ok · 1 refused, PR branch moved, or checks did not pass · 64 usage or no sign-off identity · 69 gh missing or the GitHub API call failed
"""Sign off every commit of your own GitHub pull request, without a clone.

    ph-pr-signoff.py OWNER/REPO NUMBER [--merge] [--dry-run]

WHY THIS EXISTS
    An assistant commits with `Assisted-by:` and never with `Signed-off-by:`
    (AGENTS.md section 5), so every pull request it opens is red on the DCO
    check until the human certifies it. From a clone that is `git rebase
    --signoff <base>` and a force-push; this is the same certificate for the
    case where the branch only exists on GitHub.

WHAT IT DOES
    Each commit is recreated through the git data API with the same tree, the
    same author and author date, and your `Signed-off-by:` appended to the
    trailer block (never twice). The branch is then moved to the new chain,
    unless it moved while this ran. Running it for real IS your DCO
    certification, so it refuses a commit authored by anyone else: only its
    author can sign that one off.

    --merge    wait for the checks on the new head, then rebase-merge and
               delete the branch. Never past a failing check -- on a private
               repository without branch protection nothing else stops it.
    --dry-run  say what would change; write nothing.

IDENTITY
    SIGNOFF_NAME / SIGNOFF_EMAIL, else `git config --global signoff.name` /
    `signoff.email`. Never user.email: on a work machine that is the employer's
    address, and a DCO certificate in the wrong name is worse than none.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

EX_OK, EX_FAIL, EX_USAGE, EX_UNAVAILABLE, EX_INTERRUPT = 0, 1, 64, 69, 130

# GitHub's pull-request commit listing stops at 250; past that a PR cannot be
# read whole through the API, and signing a truncated chain would move the
# branch onto it and drop the rest.
MAX_COMMITS = 250
# How long to wait for the checks of freshly pushed commits to register before
# handing over to `gh pr checks --watch`, which reports "no checks" otherwise.
CHECKS_REGISTER_S = 120

TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: \S")


class Refused(Exception):
    """The answer is no: exit 1."""


class Unavailable(Exception):
    """GitHub could not be asked: exit 69."""


def _gh(argv, data=None, capture=True):
    """The one place that runs gh; tests replace it."""
    return subprocess.run(
        ["gh", *argv], input=None if data is None else json.dumps(data),
        capture_output=capture, text=True)


def api(*args, data=None):
    argv = ["api", *args] + ([] if data is None else ["--input", "-"])
    p = _gh(argv, data)
    if p.returncode:
        raise Unavailable(f"gh api {' '.join(args)}: "
                          f"{(p.stderr or p.stdout or '').strip()}")
    return json.loads(p.stdout) if p.stdout.strip() else None


def with_signoff(message, sob):
    """`message` with `sob` at the end of its trailer block, or starting one.

    Pure, because it is the part that writes into someone's history. A last
    paragraph counts as a trailer block only when every line is `Key: value`,
    so prose that happens to hold a colon gets a new paragraph instead.
    """
    body = message.rstrip("\n")
    if sob in (line.strip() for line in body.split("\n")):
        return message
    paragraphs = body.split("\n\n")
    if len(paragraphs) > 1 and all(TRAILER.match(line)
                                   for line in paragraphs[-1].split("\n")):
        return body + "\n" + sob + "\n"
    return body + "\n\n" + sob + "\n"


def identity(env):
    def git_config(key):
        try:
            return subprocess.run(["git", "config", "--global", key],
                                  capture_output=True, text=True,
                                  env=dict(env)).stdout.strip()
        except OSError:
            return ""
    return (env.get("SIGNOFF_NAME") or git_config("signoff.name"),
            env.get("SIGNOFF_EMAIL") or git_config("signoff.email"))


def pr_commits(repo, number, expected):
    if expected > MAX_COMMITS:
        raise Refused(f"{expected} commits is more than the API lists "
                      f"({MAX_COMMITS}); sign this one off from a clone")
    commits, page = [], 1
    while True:
        batch = api(f"repos/{repo}/pulls/{number}/commits?per_page=100&page={page}")
        commits += batch
        if len(batch) < 100:
            break
        page += 1
    if len(commits) != expected:
        raise Refused(f"read {len(commits)} commits, the PR says {expected}; "
                      "it changed while reading, run again")
    return commits


def sign(repo, number, name, email, dry, out=print):
    """(rewritten, head): whether the branch changed (or would, on a dry run),
    and the head sha it carries now."""
    sob = f"Signed-off-by: {name} <{email}>"
    pr = api(f"repos/{repo}/pulls/{number}")
    if pr["state"] != "open":
        raise Refused(f"PR #{number} is {pr['state']}")
    if (pr["head"].get("repo") or {}).get("full_name") != repo:
        raise Refused("the PR comes from a fork; its author signs it off "
                      "from a clone")
    ref, head = pr["head"]["ref"], pr["head"]["sha"]
    commits = pr_commits(repo, number, pr["commits"])

    foreign = [c["sha"][:10] for c in commits
               if c["commit"]["author"]["email"].lower() != email.lower()]
    if foreign:
        raise Refused(f"commits {', '.join(foreign)} are authored by someone "
                      "else; only their author can sign them off")
    merges = [c["sha"][:10] for c in commits if len(c["parents"]) != 1]
    if merges:
        # Recreated with one parent, a merge would silently lose its other side.
        raise Refused(f"commits {', '.join(merges)} are merges; rebase the "
                      "branch first")

    parent = commits[0]["parents"][0]["sha"]
    changed = False
    for c in commits:
        obj = api(f"repos/{repo}/git/commits/{c['sha']}")
        msg = with_signoff(obj["message"], sob)
        title = obj["message"].split("\n", 1)[0]
        if msg == obj["message"] and parent == obj["parents"][0]["sha"]:
            out(f"  {obj['sha'][:10]} already signed off")
            parent = obj["sha"]
            continue
        changed = True
        if dry:
            out(f"  would sign off {obj['sha'][:10]} {title}")
            parent = obj["sha"]
            continue
        new = api(f"repos/{repo}/git/commits", data={
            "message": msg,
            "tree": obj["tree"]["sha"],
            "parents": [parent],
            "author": obj["author"],
            "committer": {"name": name, "email": email},
        })
        out(f"  {obj['sha'][:10]} -> {new['sha'][:10]} {title}")
        parent = new["sha"]

    if dry or not changed:
        return changed, head
    # Nothing is referenced until this PATCH, so every refusal above leaves the
    # branch exactly as it was. This is the last point a push can be lost.
    if api(f"repos/{repo}/pulls/{number}")["head"]["sha"] != head:
        raise Refused("the PR branch moved while signing; nothing was changed, "
                      "run again")
    api(f"repos/{repo}/git/refs/heads/{ref}", "-X", "PATCH",
        data={"sha": parent, "force": True})
    out(f"PR #{number}: branch {ref} now signed off")
    return True, parent


def merge(repo, number, head, changed, out=print):
    if changed:
        # Poll, never sleep (brain/laws/poll-never-sleep.md): the new commits'
        # checks take a moment to register, and a watch started before that
        # sees "no checks" and fails.
        deadline = time.monotonic() + CHECKS_REGISTER_S
        while (not api(f"repos/{repo}/commits/{head}/check-runs?per_page=1")
               ["total_count"] and time.monotonic() < deadline):
            time.sleep(5)
    if _gh(["pr", "checks", str(number), "-R", repo, "--watch", "--fail-fast"],
           capture=False).returncode:
        raise Refused(f"PR #{number}: checks did not pass; not merged")
    # --match-head-commit: merge what was checked, not whatever was pushed since.
    if _gh(["pr", "merge", str(number), "-R", repo, "--rebase",
            "--delete-branch", "--match-head-commit", head],
           capture=False).returncode:
        raise Refused(f"PR #{number}: gh pr merge failed")
    out(f"PR #{number}: merged")


def main(argv=None, env=os.environ):
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Identity: SIGNOFF_NAME/SIGNOFF_EMAIL or git config --global "
               "signoff.name/signoff.email (never user.email).")
    ap.add_argument("repo", metavar="OWNER/REPO")
    ap.add_argument("number", type=int, metavar="NUMBER")
    ap.add_argument("--merge", action="store_true",
                    help="wait for checks, then rebase-merge and delete the branch")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change, write nothing")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return EX_OK if exc.code == 0 else EX_USAGE
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        print(f"not OWNER/REPO: {args.repo!r}", file=sys.stderr)
        return EX_USAGE

    name, email = identity(env)
    if not (name and email):
        print("set your sign-off identity first:\n"
              "  git config --global signoff.name 'Your Name'\n"
              "  git config --global signoff.email you@example.org",
              file=sys.stderr)
        return EX_USAGE
    try:
        changed, head = sign(args.repo, args.number, name, email, args.dry_run)
        if args.dry_run:
            print("dry run: nothing written")
        elif args.merge:
            merge(args.repo, args.number, head, changed)
    except Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return EX_FAIL
    except Unavailable as exc:
        print(exc, file=sys.stderr)
        return EX_UNAVAILABLE
    except FileNotFoundError:
        print("gh is not installed: https://cli.github.com", file=sys.stderr)
        return EX_UNAVAILABLE
    except KeyboardInterrupt:
        return EX_INTERRUPT
    return EX_OK


if __name__ == "__main__":
    sys.exit(main())
