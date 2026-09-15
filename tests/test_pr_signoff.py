#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ph-pr-signoff.py: the owner's DCO certificate, applied through the GitHub API.

WHY gh IS FAKED AT ONE FUNCTION
    The tool rewrites someone's pull request branch, and the only honest way to
    test that is to watch what it would send. Every call to gh goes through
    `_gh`, so the fake below answers the API from a small in-memory repository
    and records every write -- no network, no token, and a test that fails if a
    refusal still created or moved anything.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ph_pr_signoff", ROOT / "tools" / "ph-pr-signoff.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

REPO = "owner/repo"
ME = ("Owner Person", "owner@example.org")
SOB = "Signed-off-by: Owner Person <owner@example.org>"
ENV = {"SIGNOFF_NAME": ME[0], "SIGNOFF_EMAIL": ME[1]}


# ------------------------------------------------------------ trailer block --

def test_a_subject_only_message_gets_a_new_paragraph():
    assert tool.with_signoff("subj", SOB) == "subj\n\n" + SOB + "\n"
    # porthole's own subjects are `area: change`, which reads as a trailer.
    assert tool.with_signoff("tools: add x\n", SOB) == "tools: add x\n\n" + SOB + "\n"


def test_a_body_without_trailers_gets_a_new_paragraph():
    msg = "subj\n\nWhy this change.\n"
    assert tool.with_signoff(msg, SOB) == "subj\n\nWhy this change.\n\n" + SOB + "\n"


def test_an_existing_trailer_block_is_extended_after_assisted_by():
    """The convention: Assisted-by first, the human's sign-off last."""
    msg = "subj\n\nbody\n\nAssisted-by: Claude\n"
    assert tool.with_signoff(msg, SOB) == (
        "subj\n\nbody\n\nAssisted-by: Claude\n" + SOB + "\n")


def test_an_already_signed_message_is_unchanged():
    msg = "subj\n\nAssisted-by: Claude\n" + SOB + "\n"
    assert tool.with_signoff(msg, SOB) is msg


def test_prose_with_a_colon_is_not_a_trailer_block():
    msg = "subj\n\nbody text: with colon words here\n"
    assert tool.with_signoff(msg, SOB) == msg + "\n" + SOB + "\n"


# ------------------------------------------------------------------ fake gh --

class FakeGitHub:
    """A PR of `authors` commits on top of `base`, answered the way gh does."""

    def __init__(self, authors=(ME[1],), fork=False, moved=False, checks_rc=0,
                 messages=None):
        self.writes, self.merged, self.checked = [], [], False
        self.moved = moved
        self.checks_rc = checks_rc
        parent = "base0000000"
        self.commits = []
        for i, email in enumerate(authors):
            sha = "c%010d" % i
            self.commits.append({
                "sha": sha, "parents": [{"sha": parent}],
                "commit": {"author": {"email": email}},
                "message": (messages or {}).get(i, "change %d\n\nAssisted-by: Claude\n" % i),
                "author": {"name": "A", "email": email, "date": "2026-09-15T10:00:0%dZ" % i},
            })
            parent = sha
        self.pr = {"state": "open", "commits": len(self.commits),
                   "head": {"ref": "topic/x", "sha": parent,
                            "repo": {"full_name": "someone/fork" if fork else REPO}}}
        self.reads_of_pr = 0

    def __call__(self, argv, data=None, capture=True):
        ok = lambda body: types.SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        if argv[0] == "pr":
            if argv[1] == "checks":
                self.checked = True
                return types.SimpleNamespace(returncode=self.checks_rc, stdout="", stderr="")
            self.merged.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        path = argv[1]
        payload = None if data is None else json.loads(data) if isinstance(data, str) else data
        if path == f"repos/{REPO}/pulls/1":
            self.reads_of_pr += 1
            pr = json.loads(json.dumps(self.pr))
            if self.moved and self.reads_of_pr > 1:
                pr["head"]["sha"] = "pushedmeanwhile"
            return ok(pr)
        if path.startswith(f"repos/{REPO}/pulls/1/commits"):
            page = int(path.rsplit("page=", 1)[1])
            return ok(self.commits[(page - 1) * 100:page * 100])
        if path == f"repos/{REPO}/git/commits":
            self.writes.append(("commit", payload))
            return ok({"sha": "n%010d" % len(self.writes)})
        if path.startswith(f"repos/{REPO}/git/commits/"):
            sha = path.rsplit("/", 1)[1]
            c = next(c for c in self.commits if c["sha"] == sha)
            return ok({"sha": sha, "message": c["message"], "author": c["author"],
                       "tree": {"sha": "tree" + sha}, "parents": c["parents"]})
        if path.startswith(f"repos/{REPO}/git/refs/heads/"):
            assert "PATCH" in argv
            self.writes.append(("ref", path, payload))
            return ok({})
        if "/check-runs" in path:
            return ok({"total_count": 1})
        raise AssertionError(f"unexpected gh call {argv}")


def main(argv, env=ENV):
    """The tool's own output is for a person; the runner reads only our last line."""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return tool.main(argv, env=env)


def run(fake, *args):
    tool._gh = fake
    return main([REPO, "1", *args])


# ------------------------------------------------------------------- flows --

def test_signing_keeps_tree_author_and_date_and_moves_the_branch():
    gh = FakeGitHub(authors=(ME[1], ME[1]))
    assert run(gh) == 0
    commits = [w[1] for w in gh.writes if w[0] == "commit"]
    assert len(commits) == 2
    first, second = commits
    assert first["tree"] == "treec0000000000"
    assert first["author"] == gh.commits[0]["author"], "author date must survive"
    assert first["parents"] == ["base0000000"]
    assert second["parents"] == ["n0000000001"], "the chain is rebuilt in order"
    assert first["message"].endswith("Assisted-by: Claude\n" + SOB + "\n")
    ref = [w for w in gh.writes if w[0] == "ref"]
    assert ref == [("ref", f"repos/{REPO}/git/refs/heads/topic/x",
                    {"sha": "n0000000002", "force": True})]
    assert not gh.merged


def test_a_dry_run_writes_nothing():
    gh = FakeGitHub()
    assert run(gh, "--dry-run", "--merge") == 0
    assert gh.writes == [] and gh.merged == [] and not gh.checked


def test_a_commit_by_someone_else_is_refused_before_any_write():
    gh = FakeGitHub(authors=(ME[1], "other@example.org"))
    assert run(gh) == 1
    assert gh.writes == []


def test_a_pull_request_from_a_fork_is_refused():
    gh = FakeGitHub(fork=True)
    assert run(gh) == 1
    assert gh.writes == []


def test_more_commits_than_the_api_lists_is_refused():
    gh = FakeGitHub(authors=(ME[1],) * 251)
    assert run(gh) == 1
    assert gh.writes == []


def test_a_branch_that_moved_while_signing_is_not_overwritten():
    """The new commits may exist, but nothing references them: the push that
    landed meanwhile must survive."""
    gh = FakeGitHub(moved=True)
    assert run(gh, "--merge") == 1
    assert not [w for w in gh.writes if w[0] == "ref"]
    assert not gh.merged


def test_a_failing_check_is_never_merged_past():
    gh = FakeGitHub(checks_rc=1)
    assert run(gh, "--merge") == 1
    assert gh.checked and gh.merged == []


def test_a_signed_branch_is_merged_at_its_new_head():
    gh = FakeGitHub()
    assert run(gh, "--merge") == 0
    (argv,) = gh.merged
    assert argv[argv.index("--match-head-commit") + 1] == "n0000000001"


def test_passing_checks_merge_exactly_the_head_that_was_checked():
    gh = FakeGitHub(messages={0: "done\n\n" + SOB + "\n"})
    assert run(gh, "--merge") == 0
    assert gh.writes == [], "an already signed PR is not rewritten"
    (argv,) = gh.merged
    assert "--rebase" in argv and "--delete-branch" in argv
    assert argv[argv.index("--match-head-commit") + 1] == gh.pr["head"]["sha"]


# ----------------------------------------------------------- exits, identity --

def test_missing_gh_is_unavailable_not_a_failure():
    def absent(argv, data=None, capture=True):
        raise FileNotFoundError("gh")
    assert run(absent) == 69


def test_bad_usage_is_64():
    tool._gh = FakeGitHub()
    assert main([REPO]) == 64
    assert main(["not-a-repo", "1"]) == 64


def test_identity_is_never_taken_from_user_email():
    with tempfile.TemporaryDirectory() as home:
        cfg = os.path.join(home, "gitconfig")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home,
               "GIT_CONFIG_GLOBAL": cfg, "GIT_CONFIG_NOSYSTEM": "1"}
        with open(cfg, "w") as f:
            f.write("[user]\n\tname = Work Me\n\temail = me@employer.example\n")
        tool._gh = FakeGitHub()
        assert main([REPO, "1", "--dry-run"], env=env) == 64
        with open(cfg, "a") as f:
            f.write("[signoff]\n\tname = Owner Person\n\temail = owner@example.org\n")
        assert tool.identity(env) == ME


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
