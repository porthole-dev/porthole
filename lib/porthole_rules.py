#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The rules, in one place, with the thing that enforces each one named.

WHY THIS FILE EXISTS
    The rules an agent must follow were written in four places, and they had
    already drifted apart:

      AGENTS.md section 1        10 rules
      skills/porthole-bringup    6, missing the ssh timeout and "prove it ran"
      brain/workflow/agent-protocol.md   4
      lib/porthole_cmd_brief.py  6, missing "never ask for host root" and
                                 "never hardcode" -- and this is the one an
                                 agent actually executes, because section 9
                                 tells it to run `porthole brief --json` first

    So which rules an agent got depended on which surface it happened to read.
    That is the mechanism docs/HANDOFF-contribution-rules.md blames for a
    device serial reaching a public branch: faced with 26 KB of undifferentiated
    prose an agent either weights everything equally, or picks -- and picking is
    how a session carefully avoided a trailer while publishing a serial.

    This module is not a fifth place. It is `brief.RULES` completed and
    promoted: `brief` imports it, AGENTS.md and the skill carry blocks
    generated from it, and tests/test_rules.py fails if any of them drift.

MUST VERSUS SHOULD, HONESTLY
    A MUST is machine-checkable and enforced, and `enforced_by` names the file
    that does it. If nothing can check it, it is a SHOULD however strongly
    anyone feels -- "one logical change per commit" is a SHOULD because no test
    can decide it, not because it does not matter. The discipline is the point:
    once one MUST is decorative, they all read as decorative.

    tests/test_rules.py turns that into a build failure, which is precisely the
    defect that let the serial through. "No personal information" was a MUST in
    the PR template with an enforcer that did not cover it, and nothing said so.

    Regenerate the derived blocks with:  python3 lib/porthole_rules.py --write
"""
from __future__ import annotations

import pathlib
import re
import sys

MUST, SHOULD = "MUST", "SHOULD"

BEGIN = "<!-- BEGIN GENERATED RULES -->"
END = "<!-- END GENERATED RULES -->"


class Rule:
    def __init__(self, id, level, statement, why, enforced_by=(), session=False):
        self.id = id
        self.level = level
        self.statement = statement          # one sentence, imperative
        self.why = why                      # one sentence, or a brain note id
        self.enforced_by = tuple(enforced_by)
        # Shown by `porthole brief`. Deliberately a handful: brief's own note
        # said "a wall of text gets skimmed, and these have each cost a real
        # session", and that judgement survives being moved here.
        self.session = session

    def __repr__(self):
        return f"<Rule {self.id} {self.level}>"


RULES = [
    # ------------------------------------------------- working on a device --
    Rule("no-hand-rolling", MUST,
         "Never hand-roll what a tool already does",
         "writing `ssh ... reboot` or `sleep 60` means you have not found the "
         "tool yet -- `porthole tools --grep <what>`",
         ["tests/test_tools.py::test_every_tool_declares_the_four_fields"],
         session=True),

    Rule("device-mutex", MUST,
         "Take the device mutex, declaring the state you need",
         "the-lock-says-who-not-what; exit 75 means retry, exit 76 means "
         "something must move the device first",
         ["tests/test_tools.py::test_tools_that_need_a_device_mention_the_mutex_or_use_the_lib"],
         session=True),

    Rule("hand-back-a-device-you-did-not-set", SHOULD,
         "Found the device in a state you did not put it in? Say so and hand back",
         "it is usually someone else's measurement in progress, not a fault, "
         "and recovering it destroys their run",
         session=True),

    Rule("confirm-before-irreversible", MUST,
         "Confirm before anything irreversible",
         "flashing, set_active, thermal ramps -- a bad image on the wrong slot "
         "leaves a device that will not boot and cannot be talked to",
         ["tests/test_cli_rules.py::test_verbs_escaping_their_scope_require_yes"],
         session=True),

    Rule("no-host-root", MUST,
         "Never ask for host root; builds go through `porthole sandbox`",
         "the sandbox grants zero standing host privilege, and a tool that "
         "escalates on the host is the one bug this design exists to prevent",
         ["tests/test_cli.py::test_init_prints_the_sudoers_snippet_rather_than_applying_it",
          "tests/test_sandbox_container.py::test_up_argv_maps_container_root_to_our_uid"],
         session=True),

    Rule("no-hardcoded-values", MUST,
         "Never hardcode an IP, username, slot letter or package name",
         "every one of them comes from the config layer; a hardcoded value is "
         "a tool that works on exactly one desk",
         ["tests/test_tools.py::test_no_hardcoded_gadget_ip_outside_config"],
         session=True),

    Rule("ssh-timeout-on-reset", SHOULD,
         "Put a timeout on every ssh in anything that deliberately induces a reset",
         "'the device stopped answering' is the expected outcome there, and an "
         "untimed command wedges the lock against every other agent"),

    Rule("ssh-shared-options", MUST,
         "Every ssh and scp in the build path passes the shared options",
         "without them the device key is never offered, which is why "
         "`porthole build mod` silently failed to authenticate",
         ["tests/test_tools.py::test_the_build_path_never_invokes_ssh_without_the_shared_options"]),

    Rule("no-sleep-after-a-build-verb", SHOULD,
         "Do not sleep after a build verb",
         "poll-never-sleep; every rung returns when the device is back, not "
         "when it was asked to move"),

    # ------------------------------------------------ reporting a result ----
    Rule("prove-it-ran", SHOULD,
         "Prove the code under test actually ran, and decide the control first",
         "every-test-needs-a-positive-control; a null from a path that never "
         "executed is not a refutation",
         session=True),

    Rule("contribute-what-you-learn", SHOULD,
         "Write down anything that would have saved someone a session",
         "`porthole brain new <id>`, then lint, then submit. A session that "
         "learned something and wrote nothing down is unfinished",
         session=True),

    Rule("state-what-you-verified", SHOULD,
         "In a review, say which claims you verified by execution and which you read",
         "the failure mode is not rudeness, it is a confident review of code "
         "nobody ran -- and an agent is the likeliest author of one",
         [".github/PULL_REQUEST_TEMPLATE.md"]),

    # ---------------------------------------------------------- publishing --
    Rule("no-secrets", MUST,
         "Never publish anything on the sensitive list",
         "docs/HANDOFF-contribution-rules.md section 4.5; publication is "
         "irreversible and redaction is free",
         ["tests/test_secrets.py",
          ".githooks/commit-msg", ".githooks/pre-push"]),

    Rule("attribution-trailers", MUST,
         "If an AI helped, disclose it with `Assisted-by:` -- never as a "
         "co-author, sign-off, session or generated-with line. Sign off only "
         "what is bound upstream",
         "Co-authored-by is a human-only tag and a sign-off is a DCO "
         "certificate only its author can give, so CI fails a wrong "
         "attribution on the commit message, the pull request body or the "
         "issue body, on all three surfaces. Assisted-by is disclosure, never "
         "a requirement. A sign-off is NOT required on our own pull requests: "
         "a maintainer reading the diff and merging certifies those, and a gate that "
         "was red on every agent branch until a human ran a tool to add the "
         "line taught people to clear it without reading",
         [".githooks/commit-msg", "tests/test_trailers.py",
          ".github/workflows/ci.yml",
          ".github/workflows/issue-trailers.yml"],
         session=True),

    Rule("brain-index-current", MUST,
         "A new brain note is reindexed in the same commit",
         "eight commits added a note and never ran `make brain-index`; a note "
         "missing from the index is a note nobody finds, and the index is what "
         "an agent is pointed at first",
         ["tests/test_brain.py::test_the_index_is_current"],
         session=True),

    Rule("pr-after-the-work", SHOULD,
         "Open the pull request after the work is done, not partway through",
         "a finding written mid-session is a draft: the a540 corruption note "
         "was reversed by its own next measurement, and a body filed early "
         "describes a conclusion that no longer holds",
         [".github/PULL_REQUEST_TEMPLATE.md"],
         session=True),

    Rule("hooks-installed", SHOULD,
         "Point this clone at the hooks once: "
         "`git config core.hooksPath .githooks`",
         "git ignores in-repo hooks until told, so a fresh clone has the "
         "secret scanner and the attribution check both switched off and no "
         "way to notice; `porthole brief` says which clones do",
         ["lib/porthole_cmd_brief.py"],
         session=True),

    # ------------------------------------------------- contributing code ----
    Rule("make-ci-before-pushing", SHOULD,
         "Run `make ci`, not `make check`, before opening a pull request",
         "`make check` skips the smoke and python-floor jobs that CI "
         "still runs"),

    Rule("a-check-must-fail-without-its-fix", SHOULD,
         "Changing a check means showing it fail without the fix",
         "every-test-needs-a-positive-control; the slots fixture used a "
         "spelling no device emits and so held the parser bug in place"),

    Rule("tool-header-fields", MUST,
         "Every tool declares `scope:`, `needs:`, `env:` and `exits:`",
         "a tool nobody can describe without reading it is a tool nobody "
         "improves",
         ["tests/test_tools.py::test_every_tool_declares_the_four_fields"]),

    Rule("device-tools-live-in-a-profile", MUST,
         "A device-specific probe lives in `profiles/<codename>/tools/`",
         "in tools/ it reads as generic, and the next porter runs it on the "
         "wrong phone",
         ["tests/test_tools.py::test_device_scoped_tools_live_in_a_profile"]),

    Rule("stdlib-only", MUST,
         "`lib/` is stdlib-only, no exceptions",
         "the CLI must work on a bare 3.8 with nothing installed; a "
         "dependency is the one thing that would break it silently on "
         "someone else's machine",
         ["tests/test_conventions.py::test_lib_is_stdlib_only_with_no_exceptions"]),

    Rule("exit-codes-are-an-api", MUST,
         "Exit codes come from the documented table and nowhere else",
         "exit-codes-are-an-api; 69 versus 1 is the difference between 'the "
         "check did not happen' and 'the check failed'",
         ["tests/test_conventions.py::test_exit_codes_come_from_the_documented_table"]),

    Rule("python-floor", MUST,
         "Python 3.8 is the floor, and bin/porthole, the Makefile and CI agree on it",
         "a PEP 701 f-string compiled locally on 3.14 and broke every CI job",
         ["tests/test_tools.py::test_the_python_floor_is_declared_consistently"]),

    Rule("file-hygiene", MUST,
         "LF endings, a final newline, and no trailing whitespace",
         ".editorconfig says so and nothing checked it until now",
         ["tests/test_conventions.py::test_tracked_text_files_are_clean"]),

    Rule("frozen-tk-names", MUST,
         "`tk_*` shell helpers are never renamed or deleted; new ones are `ph_*`",
         "they are a compatibility surface for tools outside this repo, which "
         "is why `tk_wait_fastboot` stays despite having no callers",
         ["tests/test_conventions.py::test_the_tk_helper_surface_is_frozen"]),

    Rule("ci-runs-only-make-targets", MUST,
         "CI runs nothing but `make` targets that `make ci` also reaches",
         "three hand-kept copies of the step list is how a green `make check` "
         "kept shipping a red pipeline",
         ["tests/test_tools.py::test_ci_runs_nothing_but_make_targets_that_make_ci_also_runs"]),

    Rule("one-logical-change-per-commit", SHOULD,
         "One logical change per commit, and the body says why rather than what",
         "not machine-decidable, and calling it a MUST would make every MUST "
         "read as decorative"),

    Rule("a-decision-that-must-not-be-wrong-is-pure", SHOULD,
         "A decision that must not be wrong is a pure function",
         "`_classify`'s own docstring: a pure function is one that can be "
         "wrong in a test instead of on a device"),

    Rule("comments-record-the-incident", SHOULD,
         "A comment says why the code is shaped this way and what it cost to learn",
         "it is why this codebase is legible cold; a comment that only "
         "restates the code should be deleted in review"),
]

BY_ID = {r.id: r for r in RULES}


def must(rules=None):
    return [r for r in (rules or RULES) if r.level == MUST]


def session_rules():
    """What `porthole brief` shows. Short on purpose."""
    return [r for r in RULES if r.session]


def render(rules, enforcers=True):
    """The generated block. Pure, so tests/test_rules.py can compare it to what
    is on disk without running the writer."""
    out = []
    for r in rules:
        out.append(f"- **{r.statement}** — {r.why}")
        tail = f"(`{r.id}` · **{r.level}**"
        if enforcers and r.enforced_by:
            tail += " · enforced by " + ", ".join(f"`{e}`" for e in r.enforced_by)
        elif enforcers:
            tail += " · no enforcer, and so not a MUST"
        # No two-space hard break: that is trailing whitespace, which
        # `file-hygiene` forbids and tests/test_conventions.py caught here
        # first. A soft-wrapped continuation renders the same.
        out.append(f"  {tail})")
    return "\n".join(out)


# path -> what goes between its markers. Everything here is DERIVED; edit this
# file and regenerate, never the block.
GENERATED = {
    "AGENTS.md": lambda: render(RULES),
    "skills/porthole-bringup/SKILL.md": lambda: render(session_rules(),
                                                       enforcers=False),
}


# An empty block must match too, or the very first generation is a no-op --
# which is how this file's own markers stayed empty on the first run.
_BLOCK = re.compile(re.escape(BEGIN) + r"\n(.*?)" + re.escape(END), re.S)


def block_of(root, rel):
    """What is currently between the markers in `rel`, or None if unmarked."""
    m = _BLOCK.search((root / rel).read_text())
    return m.group(1).rstrip("\n") if m else None


def write(root):
    changed = []
    for rel, make in GENERATED.items():
        path = root / rel
        text = path.read_text()
        want = make()
        new = _BLOCK.sub(lambda _: f"{BEGIN}\n{want}\n{END}", text)
        if new != text:
            path.write_text(new)
            changed.append(rel)
    return changed


if __name__ == "__main__":
    root = pathlib.Path(__file__).resolve().parent.parent
    if "--write" in sys.argv:
        for rel in write(root) or ["(nothing -- already current)"]:
            print(f"wrote {rel}")
    else:
        print(render(RULES))
