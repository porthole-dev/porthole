<!-- porthole | handoff | 2026-08-31 -->
# Handoff: the contribution rules are prose, and prose does not hold

Written 2026-08-31, after a session in which a device serial reached a public
branch, a public commit message, a public test fixture and a public review
comment — through a PR checklist that says *"no personal paths, usernames or
IPs (the tests enforce this)"* and a CI run that went green seven checks out
of seven.

Nothing here is a style opinion. Every rule below already exists somewhere in
this repo as a sentence. The defect is that **a sentence is not an enforcer**,
and this project has now paid for that three times: 35 AI trailers, then 49
more, then a serial number. The implementation is not "write the rules down".
They are written down. It is "give every rule that matters a thing that runs".

---

## 1. State right now

- `main` is at `0fbc707`. Protected: direct pushes are rejected, PRs only.
- `.githooks/commit-msg` exists and strips attribution trailers. It is the
  **only** executable governance in the repo.
- It is inert on a fresh clone. Git ignores in-repo hooks until someone runs
  `git config core.hooksPath .githooks` by hand, and nothing checks that they
  did.
- CI runs seven jobs (`tests` × 3.8/3.11/3.13, `console`, `lint`, `build`,
  `fresh-clone smoke`). **None of them runs the hook, and none scans for
  secrets.**
- `CONTRIBUTING.md` is **not in git**. It was an untracked file in the working
  tree at the start of this session and is gone now; `git log --all --
  CONTRIBUTING.md` is empty. Whatever it said was never a rule anyone could
  read from a clone.
- `AGENTS.md` (26 KB) is the real contract. `brain/laws/` holds ten inviolable
  rules. `brain/workflow/` holds five method notes. There is no index saying
  which of the three a given rule lives in, or which are binding.

## 2. What was proven this session

**The secrets check does not scan the places secrets landed.** `PERSONAL` in
`tests/test_tools.py:28` matches `/home/…`, `/var/home/…` and `user@1.2.3.4`.
Measured:

```python
PERSONAL.search("serialno:<a real 14-char serial>")   # -> None
```

It also iterates `tools()` only. The serial was in `tests/`, `profiles/`,
`brain/` and a commit message — four locations, none of them scanned, on a
check the PR template advertises as enforcing exactly this.

**A rule with a hook holds; a rule without one does not.** The trailer ban has
a hook and survived this session unscathed across eleven commits. The
"no personal information" rule has a checkbox and failed on its first real
test. Same repo, same week, same contributors.

**The checklist was ticked.** The PR carrying the serial was well-written: it
had evidence, a positive control, a documented trade-off, and green CI. A
careful contributor following the documented process shipped the leak, which
means the process — not the contributor — is what needs changing.

**Force-pushing does not undo publication.** After rewriting both branches and
deleting one, the original commits still resolve by SHA through the GitHub API.
Only GitHub Support can purge them. **The only cheap moment is before the
push.** Every rule in §6 follows from that sentence.

## 3. What was NOT proven

- Whether a serial is worth protecting at all. Nobody has decided; it was
  treated as sensitive because the author redacted it in their own PR
  description, which is evidence of intent, not policy. **§6 needs a human
  decision on what counts as sensitive before it can be implemented.**
- Whether the other rules in `AGENTS.md` are being followed. Only the two
  above were tested. A survey is part of the work, not an assumption.
- Whether contributors would accept a pre-push hook. It is proposed below
  because it is the last moment that is still cheap, but it adds friction and
  that is a maintainer's call.

## 4. The work

Six pieces. They are ordered: §4.6 is the one that makes the rest durable, and
doing it first would mean classifying rules that §4.1–§4.5 are about to change.

### 4.1 Commit convention

Already written, in three places that do not agree on their own authority:
`AGENTS.md §5`, `brain/workflow/commit-conventions.md`, `.githooks/commit-msg`.

Binding, and each needs a named enforcer:

| rule | enforcer today | needed |
|---|---|---|
| no trailers of any kind | `.githooks/commit-msg` | also a CI check, since the hook is opt-in |
| author and committer are the human | nothing | CI: flag a commit whose author is a bot or a generic identity |
| one logical change per commit | nothing | stays advisory — not machine-decidable |
| body explains why, not what | nothing | stays advisory |
| upstream-bound commits follow the destination's style | nothing | stays advisory, `brain/playbooks/90-upstreaming.md` covers it |

Add: **subject line ≤ 72 chars, imperative mood, no trailing period.** The
repo's own history already follows this; it is unwritten. Machine-checkable, so
it becomes binding rather than folklore.

Do **not** adopt Conventional Commits. This history uses the subject line to
state a finding (`build: tkmod never offered the device key, and nothing said
so`), which is more useful than a type tag, and a mechanical reformat would
destroy the one thing the log is currently good at.

### 4.2 Branch naming

No convention exists. This session produced `build-defects-and-doctor-probes`,
`slots-getvar-format`, `slots-getvar-colon`, `redfin-prep` and
`doctor-pmbootstrap-shebang-probe` — five branches, five shapes.

Proposal, deliberately loose because branch names are cheap and short-lived:

```
<area>/<what>        device/redfin-slot-policy
                     doctor/tool-execution-probe
                     build/tree-autoselect
```

`<area>` is the verb or subsystem the change touches (`build`, `doctor`,
`slots`, `pkg`, `sandbox`, `brain`, `docs`, `device`). One enforcer: CI warns
— never fails — on a branch that has no `/`. A failing build over a branch name
is exactly the kind of rule that teaches people to distrust CI.

**Delete the branch on merge.** `gh pr merge --delete-branch`, and turn on the
repo setting so it is the default rather than a thing to remember.

### 4.3 Pull requests and comments

The PR template is good and is being followed. Three additions:

1. **A secrets line that names what to look for**, replacing "no personal
   paths, usernames or IPs (the tests enforce this)" — which is now known to
   overpromise. It should say what is scanned and what is not, because a
   checklist that claims machine backing it does not have is worse than one
   that admits it is manual.
2. **"What did you verify, and how"** — this repo's culture is evidence over
   assertion, and the template does not ask for it. Every good PR in the repo
   volunteers it anyway; asking makes it the floor rather than a courtesy.
3. **"Which claims are unverified"** — the mirror of `brain/laws/`'s discipline
   about refutations. PR #4 carried a stale CI claim ("failure set identical to
   main") that was false; a prompt for it invites the author to mark it.

**Review comments.** Write for the person, not the record. The rules that
matter are few:

- Quote the evidence, not the sensitive value. This session's cleanup existed
  partly because a review comment quoted a serial in full, in public, while
  arguing that it should be redacted.
- Say what you verified and what you took on trust. "I checked the old parser
  returns `{}` for this input" is worth more than "looks right".
- Credit the finding by name when you supersede someone's implementation, and
  keep the superseded approach as a reference note if the reasoning has value.
  There is precedent:
  `brain/findings/a-shebang-probe-is-a-subset-of-running-the-tool.md`.
- Never close a contributor's PR without saying what replaced it and where.

**Agent-specific, and this is the one that needs writing down:** an agent
reviewing a PR must state which claims it verified by execution and which it
read. The failure mode is not rudeness, it is a confident review of code nobody
ran.

### 4.4 What must pass

Binding, no exceptions, in this order:

1. `make ci` locally — **not** `make check`, which skips the console, smoke and
   python-floor jobs that CI still runs.
2. All seven CI jobs green on the PR.
3. For a change to a *check*, the check must be shown to fail without the fix.
   `brain/laws/every-test-needs-a-positive-control.md` already says this; it is
   not in the PR template, and it is the rule that would have caught the
   `key: value` fixture holding the parser bug in place.

Add a CI job — `secrets` — for §4.5. It is the only new job, and it belongs in
CI rather than only in a hook because a hook cannot be relied on to exist.

### 4.5 What must never be published

**The decision this needs from a human first:** what counts as sensitive here.
The proposed list, to accept or cut:

| class | example | verdict |
|---|---|---|
| host paths and usernames | `/home/<user>/…` | already banned, already enforced in `tools/` |
| user@IP | `<user>@172.16.42.1` | already banned, already enforced in `tools/` |
| device serials | `fastboot devices` output, `getvar serialno` | **proposed: banned** — this session's leak |
| IMEI, MEID, ICCID, MAC addresses | modem and wifi probes print these freely | **proposed: banned** — stronger case than serials |
| keys and tokens | ssh private keys, `gh` tokens, `PORTHOLE_*_PASSWORD` | **proposed: banned**, obviously |
| GPS fixes from a real device | sensor probe output | **proposed: banned** |
| the pmOS gadget IP `172.16.42.1` | | **not** sensitive — a documented default, already handled |

Implementation:

- Widen `PERSONAL` and move it out of `tests/test_tools.py`, which scans
  `tools()` only, into its own `tests/test_secrets.py` that scans **everything
  git tracks** — `tools/`, `lib/`, `tests/`, `profiles/`, `brain/`, `docs/`,
  `.github/`.
- Add a `commit-msg` hook stage that scans the **message**, since the serial
  reached a commit message and no check has ever looked at one.
- Add a `pre-push` hook that scans the range being pushed. This is the last
  cheap moment; after the push, only GitHub Support can help.
- Every pattern needs a positive control, per `brain/laws/`. A secrets scanner
  that matches nothing passes silently, which is the failure mode it exists to
  prevent.
- **Redaction guidance, not just prohibition.** A brain note's evidence line
  needs the device model and the date, not the serial — that keeps the note
  reproducible without identifying the hardware. Say so where people write
  notes, or they will strip the evidence instead of the identifier.

Also worth deciding: whether `profiles/*/device.env` may ever carry a serial as
a *functional* value (multi-device selection). Today none do, and the ban is
free. If that changes, the scanner needs an allowlisted key rather than an
exception per file.

### 4.6 Reorganising the rules so agents run them

This is the piece the other five depend on, and it is the actual ask.

**The problem.** A rule lives in `AGENTS.md`, or `brain/laws/`, or
`brain/workflow/`, or a PR checkbox, or a hook, or a test — six surfaces, no
index, and nothing says which are binding. An agent reading 26 KB of `AGENTS.md`
cannot tell a law from a preference, so it either follows everything with equal
weight (slow, and it will still miss the unwritten ones) or picks, which is how
this session shipped a serial while carefully avoiding a trailer.

**The shape.** One machine-readable manifest — `rules.toml` at the repo root, or
`brain/RULES.md` with a table — where every rule has:

```
id            no-trailers
level         MUST | SHOULD
statement     one sentence, imperative
why           one sentence, or a brain note id
enforced-by   .githooks/commit-msg, tests/test_commit_msg.py
              # or: NONE, only legal for SHOULD
```

And one test — `tests/test_rules.py` — asserting:

- every `MUST` names at least one enforcer, and that enforcer exists,
- every enforcer named actually runs in `make ci`,
- every `brain/laws/*.md` has a corresponding `MUST` entry,
- no rule text is duplicated across `AGENTS.md` and the manifest with different
  wording. **Duplication with drift is worse than either copy alone**, and this
  repo already has it: `AGENTS.md §5` and
  `brain/workflow/commit-conventions.md` both state the trailer rule, and they
  do not agree on whether upstream sign-offs are an exception.

That test is what converts governance from something read into something run.
It makes "a MUST with no enforcer" a build failure, which is precisely the
defect that let the serial through: the no-personal-information rule was a MUST
in the PR template and had an enforcer that did not cover it.

**Then `AGENTS.md` shrinks.** It keeps the narrative — the traps, the reasoning,
the things an agent needs to *understand* — and cites rule ids instead of
restating them. The manifest becomes the single place a rule's text lives.

**MUST vs SHOULD, the honest split.** A `MUST` is machine-checkable and
enforced; if it cannot be checked, it is a `SHOULD` no matter how strongly
anyone feels. "One logical change per commit" is a SHOULD not because it is
unimportant but because no test can decide it. Calling it a MUST while nothing
enforces it is how a rule set loses its authority: once one MUST is decorative,
they all read as decorative.

### 4.7 Coding conventions

Mostly already true in the code and almost entirely unwritten. The job is to
state them and say which are enforced -- not to change the code, which is
consistent already.

**The precedent to copy is `tests/test_cli_rules.py`.** It enforces eighteen
CLI conventions: actions are positional not flags, every reporting verb takes
`--json`, `--device` is a selector only, a verb escaping its scope requires
`--yes`, every SPEC carries help and examples. That file is the working proof
of section 4.6's whole argument -- conventions written as tests are followed,
conventions written as prose are not. Everything below either joins it or
admits it cannot.

**Layout.** `AGENTS.md` section 4b already has the table: a CLI verb is
`lib/porthole_cmd_<name>.py` with a `SPEC` dict and is discovered rather than
registered; a generic tool is `tools/`; a device-specific probe is
`profiles/<codename>/tools/`. Keep it there and cite it by rule id.

**Python.** Measured, not asserted:

| rule | status | level |
|---|---|---|
| 3.8 floor | `make floor` runs the suite on 3.8.20 | MUST, enforced |
| stdlib only, no third-party deps | verified: every import in `lib/` is stdlib | MUST -- needs a test, trivially written |
| `from __future__ import annotations` where modern hints are used | convention, followed | SHOULD |
| 4-space indent, LF, final newline, no trailing whitespace | `.editorconfig` | MUST -- but nothing checks it |
| 80 columns | `.editorconfig` says 80; about 2% of lines exceed it (325 in `lib/`, 253 in `tools/`, 184 in `tests/`) | SHOULD -- calling it a MUST means reflowing ~760 lines for no benefit |

The stdlib-only rule is worth enforcing precisely because it holds perfectly
today: a check added now stays green and catches the first regression, while
the same check added after the first dependency lands is an argument instead.

**Exit codes are an API.** Already `AGENTS.md` section 6 and
`brain/laws/exit-codes-are-an-api.md`. The table -- 0 ok, 1 failed, 64 usage,
69 could not run, 75 locked, 76 wrong state, 124 timeout -- is binding, and 69
versus 1 is the distinction that matters most: "the check did not happen" is
not "the check failed". A test can assert no verb invents a code outside it.

**Shell.** `tk_*` names are a frozen compatibility surface: never renamed,
never deleted, even when unused (`tk_wait_fastboot` has no callers and stays).
New helpers are `ph_*`. Every tool sources `lib/porthole.sh` and carries the
four header fields `scope:`, `needs:`, `env:`, `exits:`, already enforced by
`tests/test_tools.py`. shellcheck clean, with the three documented disables in
`.shellcheckrc` and no new ones without a comment saying why.

Add, from this session: every `ssh`/`scp` in the build path passes
`"${TK_SSH_OPTS[@]}"`. Enforced now in `tests/test_tools.py`, and the reason
`porthole build mod` never offered the device key.

**Two patterns that deserve to be named rules, because both were load-bearing
this session.**

*A decision that must not be wrong is a pure function.* `_classify` (which rung
covers a change), `_autoselect_tree` (which tree to build) and `_container_cmd`
(what argv reaches the container) all take their inputs as arguments and return
a value, with the I/O left in the caller. The reason is in `_classify`'s own
docstring: "a pure function is one that can be wrong in a test instead of on a
device." SHOULD -- not machine-checkable, but the highest-value habit in this
codebase and currently folklore.

*Verify the artifact, not the exit code.* Already a law in practice:
`pmbootstrap build` writes an apk and then fails, `insmod` exits 0 without
loading, `make` returns non-zero with nothing wrong. Every rung ends by
checking what it produced. This belongs in `brain/laws/`, where today it is
only implied by `shipped-configuration-is-not-running-configuration.md`.

**Comments and docstrings -- the repo's most distinctive trait, entirely
unwritten.** A comment here records the incident, with the date and the cost:

```python
# Found by running it. PORTHOLE_NO_CCACHE was documented as the way to turn
# the compiler cache off and did nothing in the workspace, because the
# filter dropped it before podman ever saw it.
```

Not what the code does -- why it is shaped that way, and what it cost to learn.
A docstring says which failure the function prevents. This is why the codebase
is legible to someone arriving cold, and why an agent can work in it without
re-deriving the history.

It cannot be machine-checked and must not be faked, so it is a SHOULD with
teeth: a comment that only restates the code should be deleted in review. Worth
one line in the PR template, because it is the convention most likely to erode
as contributor count grows and the hardest to restore afterwards.

**Tests.** No pytest: a test file is a plain script ending
`if __name__ == "__main__": sys.exit(main())`, using `tests/_runner.py`. Every
test is hermetic -- no device, no podman, no network, no pmbootstrap. Test
names are sentences
(`test_a_relative_kernel_tree_resolves_against_the_workdir`), and the docstring
says which real failure the test pins.

And the one this session paid for twice:
`brain/laws/every-test-needs-a-positive-control.md`. The `slots` parser fixture
used a spelling no device emits, so the suite passed against a parser that
could not read a single real line. The test was not merely weak, it was holding
the bug in place. A fixture nobody has checked against reality is a test that
certifies the bug.

## 5. Anything currently unsafe

- **The serial is public.** `477881f…` and `d1dab0e…` still resolve on GitHub
  by SHA. Branches were rewritten and one was deleted; that removes the
  reachable copies and nothing else. If it matters, it needs GitHub Support.
- **`redfin-prep` was force-pushed.** Alessandro Ianne has been told, on PR #4,
  to `git reset --hard origin/redfin-prep` before pushing there again. Until he
  does, his local clone still holds the serial and will re-publish it on his
  next push.
- **The hook is opt-in.** Any clone that has not run
  `git config core.hooksPath .githooks` has no governance at all. §4.4's CI job
  is what makes this safe; until it exists, the trailer ban depends on every
  contributor having run one command nobody checks.
- `CONTRIBUTING.md` is absent from git entirely. Anyone who onboards from a
  clone gets `AGENTS.md` or nothing.

## 6. The next concrete step

Decide §4.5's sensitive list — it is the only item blocked on a human, and
§4.5's implementation and §4.6's manifest both encode the answer. Then build in
this order, because each step's enforcer is what makes the previous step's rule
real:

1. `tests/test_secrets.py`, scanning everything tracked, with a positive
   control per pattern.
2. The `secrets` CI job, plus `commit-msg` and `pre-push` hook stages.
3. A CI check that the trailer ban holds regardless of local hook config.
4. The rules manifest and `tests/test_rules.py`.
5. `AGENTS.md` shrunk to narrative plus rule ids; `CONTRIBUTING.md` written and
   committed, pointing at the manifest rather than restating it.
6. Branch-name advisory, PR template additions, and the review-comment rules
   in §4.3.
7. Coding conventions (§4.7): the stdlib-only test and the exit-code-table
   test first, since both are green today and cheap; then the written
   conventions, cited by rule id rather than restated.

Steps 1–3 close the hole this session opened. Steps 4–7 are what stop the next
one, and they are the ones that will get skipped if the list is worked from the
bottom.

**One caution for whoever implements this.** Do not let the manifest become a
second place where rules are written, drifting from the first. The test in
§4.6 forbidding duplicated-but-differently-worded rules is not bureaucratic
tidiness: `AGENTS.md` §5 and `brain/workflow/commit-conventions.md` already
disagree about upstream sign-offs today, and that disagreement is exactly how a
contributor picks whichever reading suits them.
