
> **Superseded (2026-09-15).** The hook no longer strips trailers and the
> repository was republished. The current attribution rules are in
> `AGENTS.md` §5 and `docs/CONTRIBUTING.md`; this handoff records the state
> on its date.

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
  secrets.** The second half of that is what §4.5 has since fixed.
- A root `CONTRIBUTING.md` is **not in git**. It was an untracked file in the
  working tree at the start of this session and is gone now; `git log --all --
  CONTRIBUTING.md` is empty. **`docs/CONTRIBUTING.md` however is tracked**, is
  6 KB, and is what `porthole brief` already points contributors at — a later
  reading of this section as "there is no contributing guide at all" was
  wrong, and §4.6's work corrected it.
- `AGENTS.md` (26 KB) is the real contract. `brain/laws/` holds ten inviolable
  rules. `brain/workflow/` holds five method notes. There is no index saying
  which of the three a given rule lives in, or which are binding.

## 2. What was proven this session

**The secrets check does not scan the places secrets landed.** `PERSONAL` in
`tests/test_tools.py:28` matches `/home/…`, `/var/home/…` and a
`<user>@<ipv4>` pair.
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

- Whether a serial is worth protecting at all. It was treated as sensitive
  because the author redacted it in their own PR description, which is
  evidence of intent, not policy. **Now decided — see §4.5.** Not by
  quantifying the harm: by noting that a serial has no documentary value and
  that publication is irreversible, which settles it without a threat model.
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
| no trailers of any kind, on a commit **or a pull request body** | `.githooks/commit-msg`, `tests/test_trailers.py`, the `trailers` job in `ci.yml` | done — the hook is still opt-in, the CI check is not |
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

1. `make ci` locally — **not** `make check`, which skips the smoke and
   python-floor jobs that CI still runs.
2. All six CI jobs green on the PR.
3. For a change to a *check*, the check must be shown to fail without the fix.
   `brain/laws/every-test-needs-a-positive-control.md` already says this; it is
   not in the PR template, and it is the rule that would have caught the
   `key: value` fixture holding the parser bug in place.

No new CI job for §4.5, which the proposal had asked for. `make test` globs
`tests/test_*.py`, so `tests/test_secrets.py` is picked up and runs in all three
matrix jobs already. The requirement was "it runs in CI regardless of anyone's
hook config", and the glob meets it. A hand-added job would be a fourth copy of
the step list the Makefile's own header warns about, and `tests/test_tools.py`
fails on a job naming a target `make ci` does not reach.

### 4.5 What must never be published

**Decided 2026-08-31**, after measuring the proposed list against the repo.
Two of its rows were claims about code that do not hold, and the one class
this repo has actually had to redact was missing from it.

**The rule is one sentence, because a list gets argued row by row and a
principle decides the rows nobody thought of:**

> Never publish a value that is **stable** and ties an artifact to one
> physical device, one person, one private network or one account, and that
> the note does not need in order to stay reproducible. Publish what is
> random, ephemeral, public by design, or load-bearing evidence.

That is why the serial needed no threat model. An evidence line needs
`taimen, 2026-08-31`; it has never needed the serial. The documentary value is
zero, publication is irreversible, redaction is free — an asymmetry that
lopsided settles the case without anyone ruling on how bad a leaked serial
actually is.

| class | verdict | caught by |
|---|---|---|
| host paths and usernames (`/home/<user>/…`) | banned | `PERSONAL`, widened to every tracked file |
| `<user>@<ipv4>` | banned | `PERSONAL` |
| device serials | banned | the label, not the value — see below |
| IMEI, MEID, ICCID, IMSI | banned | the label |
| private keys, tokens, passwords | banned | `BEGIN … PRIVATE KEY`, `gh[pousr]_…`, `*_PASSWORD=<literal>` |
| MAC addresses **whose locally-administered bit is clear** | banned | exact, and zero false positives today |
| the SSID and BSSIDs of a network you do not own | banned | BSSID by the MAC rule; the SSID is manual |
| GPS or any other location fix | banned | manual |
| screenshots, screen and camera captures of a booted device | banned | manual — no scanner will ever read one |

**Explicitly not sensitive**, and this half of the list carries as much weight
as the other. A scanner that cries wolf teaches people `--no-verify`, and a
rule set that over-reaches loses exactly the authority §4.6 is trying to
build:

- the pmOS gadget IP `172.16.42.1` — a documented default, already carved out
- **randomised MAC addresses** — see below
- the contributor's own name and email in authorship. They are in every commit
  by design, and `SECURITY.md` tells reporters to find the maintainer's
  address in the git history. Scrubbing them is the over-correction to guard
  against, not a fix.
- device codenames, SoC names, kernel and commit hashes, package versions

**The MAC rule, and why the proposed blanket ban was cut.** Seven MAC-shaped
strings are already tracked:

```
brain/findings/taimen-has-no-factory-wlan-mac.md      56:61:bd…  86:c6:a5…  02:00:B6…
brain/traps/usb-gadget-rerandomises-the-host-mac.md   92:fe:35…  b2:c4:15…  36:31:25…
```

Every one is a **randomly generated** address, and both notes are *about* the
randomisation — the MAC is the evidence, not a leak. A blanket ban deletes two
findings to protect nothing.

The discriminator is free and already in the bytes. A locally-administered
address has bit 1 of its first octet set — second hex digit in `2367abef` — and
a factory-burned address from an OUI never does, by definition, nor does a
real AP's BSSID. All seven tracked MACs have it set. So the rule is **ban a MAC
whose locally-administered bit is clear**: it passes the entire existing tree
with no exceptions and no allowlist, and catches every address that identifies
real hardware.

**The class the proposed list missed: a private network's identity.** A BSSID
is a key into commercial wifi geolocation databases — Google, Apple and Mozilla
resolve one to a street address. Publishing `TEST-SSID`'s three BSSIDs would
pin a contributor's workplace to a building, a stronger location leak than any
GPS fix this repo can currently produce. Someone already understood that and
hand-redacted them to `<ap-ch36>`, `<ap-ch140>` and `<ap-ch1>`;
the convention was never written down, and `tools/ph-wifi-soak.sh:108` emits
`"bssid":"%s"` into every heartbeat line by construction.

**Two rows kept, with their justification corrected.** The proposal said modem
and wifi probes "print these freely". They do not:
`grep -riE "imei|meid|iccid|imsi"` over the whole repo hits exactly one line,
this table's own earlier draft. `tools/ph-daily-audit.sh` runs `mmcli -L` and
`mmcli -m any | grep -iE 'state:|lock'`, neither of which prints an equipment
id. GPS is the same — no NMEA, no coordinates, only `unit geoclue.service`.
Both stay banned, because the cost is nothing and an IMEI is the strongest
identifier in the phone, but the **real vector is pasting full `mmcli -m any`
output**, and saying so is what keeps the rule from rotting. §4.6's test asks
what enforces every MUST; a MUST defended by a false claim about the code is
the first one to be quietly dropped.

**Serials: banned outright, no functional exception.** No `device.env` carries
one and there is no `PORTHOLE_SERIAL`, so the ban costs nothing today. If
multi-device selection ever needs one, that change adds an allowlisted key and
its own positive control, decided with the real requirement in hand rather
than as a guessed-at exception sitting there waiting to be used.

**Match the label, not the value.** `git grep -IoE "\b[0-9A-Za-z]{12,20}\b"`
returns 1,889 hits — register dumps, apk checksums, commit hashes. A shape
check for "a serial" is unshippable and always will be. But pasted probe
output arrives with its label attached, every time: `serialno:`,
`getvar:serialno`, a `fastboot devices` line, `imei:`. Match those. It covers
the way these values actually reach a file, at a false-positive cost of zero.

**Say what is not covered.** A bare serial with no label, an SSID string, a
screenshot: no regex catches any of them. §4.3 is right that a checklist
claiming machine backing it does not have is worse than one admitting it is
manual — that overpromise is precisely what let this leak through. The PR line
must name what is scanned and what is a human's job.

**Redact, do not delete.** The convention already exists in the tree and only
needs writing down: replace the identifier with a stable angle-bracket
pseudonym — `<ap-ch36>`, `TEST-SSID` — so the note stays readable and the
analysis stays reproducible. *Stable* is the load-bearing word: collapsing
three BSSIDs into one token would have destroyed the finding that the three
are not interchangeable. Say this where people write notes, or they will strip
the evidence instead of the identifier.

**Implemented 2026-08-31.** `lib/porthole_secrets.py` holds the rules;
`tests/test_secrets.py` runs them over everything `git ls-files` returns and is
picked up by `make test`'s glob, so it runs in all three CI matrix jobs with no
new job and no new Makefile target. `.githooks/commit-msg` scans the message —
the surface no check in this repo had ever read — and `.githooks/pre-push`
scans the range being pushed, which is the last cheap moment. `logs/` is in
`.gitignore`: `tools/ph-capture.sh` writes there by default and brain notes
cite it as evidence, so it fills up constantly, and it was untracked but not
ignored — one `git add -A` from publication.

**What it found on its first clean run.** Three real leaks, none of them the
one everybody was looking at:

- `docs/HANDOFF-build-tree-selection.md:156` carried the maintainer's actual
  home path, in a line quoting `porthole doctor` output.
- `docs/SANDBOX-PROVISIONING.md:197` carried the same login as `<user>@<ipv4>`.
- `tests/test_slots.py:36` still held the redfin serial that caused this
  document — 8 of its 14 characters, masked with `XXXXXX` and left in the
  fixture. Nothing asserts on it; it is now `<serial>`, and the comment above
  it carries the model and the date, which is what an evidence line actually
  needs.

All three are redacted. The first two had been public for weeks and no review
caught either, which is the argument for the scanner compressed into one line.

**The first draft flagged 43 things and 40 were noise** — `/home/pmos` (a fixed
account inside the pmbootstrap chroot, not anyone's desk), `NOPASSWD: ALL` (a
sudoers directive, not a credential), `olduser@172.16.42.1` across six test
fixtures. That run is worth recording, because a scanner at that signal ratio
is *worse* than none: people learn to reach for `--no-verify`, and then the
real hit goes through with the noise. The fix came from re-reading the
principle above — what both path rules protect is the **login**, never the path
or the address, since `172.16.42.1` is a documented default either way. So the
rules capture the username and check it against a closed `PLACEHOLDER_NAMES`
set of the stand-ins this tree already uses. Adding your own login to that set
is a visible line in a diff, which is exactly what the sentence version of this
rule never made anyone do.

**The pre-push hook refused this change's own first push**, and was right to.
A diff is a flat list of added lines with no idea which file any of them came
from, so every one of the scanner's own controls read as a leak on the way out.
Grouping the added lines by file is what carries the single exemption to the
push surface, and it is a pure function with its own test, because it is the
only place a push is decided — `_classify`'s docstring says why in §4.7: a pure
function is one that can be wrong in a test instead of on a device.

**Two classes were upgraded from manual while implementing:**

- **Screenshots.** No regex reads an image, but the extension is exact. Raster
  formats are refused outright; SVG is not, because a diagram is not a
  photograph of someone's device. Zero are tracked today, so the rule is free
  and stays green until the day it matters.
- **Both directions, on every rule.** `brain/laws/every-test-needs-a-positive-control.md`
  is read strictly here: each rule must match its own positive control *and*
  stay silent on a counter-example. For the MAC rule that counter-example is a
  randomised address out of `taimen-has-no-factory-wlan-mac.md`, so the suite
  fails the moment the rule widens into eating the evidence in the two findings
  it was written around. A one-directional test would have let exactly that
  happen.

**Still manual**, and the PR template now says so rather than overpromising a
second time: a bare serial with no label, an SSID, a location, and anything
inside an image.

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

**Implemented 2026-08-31.** `lib/porthole_rules.py` is the manifest — 27
rules, 16 MUST and 11 SHOULD — and `tests/test_rules.py` is the test that makes
it binding. Three things came out differently from the proposal, each because
measuring it disagreed.

**`rules.toml` is impossible here.** `tomllib` arrived in 3.11, the declared
floor is 3.8, and `lib/` is stdlib-only — so a TOML manifest means shipping a
parser to read the file that lists the rule against shipping parsers. The
manifest is Python, which is also what the repo already does three times over
(`porthole_milestones.py`, `porthole_cmd_blobs.py`, and `brief.RULES` itself).

**It is not a fifth place, and that was the real finding.** The agent rules
were in four, and they had already drifted:

| rule | `AGENTS.md` §1 | `SKILL.md` | `agent-protocol.md` | `brief.RULES` |
|---|---|---|---|---|
| never hand-roll a tool | ✓ | ✓ | ✓ | ✓ |
| device mutex | ✓ | ✓ | ✓ | ✓ |
| hand back a device you did not set | ✓ | ✓ | ✓ | ✓ |
| confirm before irreversible | ✓ | ✓ | — | ✓ |
| **never ask for host root** | ✓ | ✓ | — | **missing** |
| **never hardcode a value** | ✓ | ✓ | — | **missing** |
| **ssh timeout on a reset** | ✓ | — | ✓ | **missing** |
| **prove the code under test ran** | §2 | — | ✓ | ✓ |
| do not sleep after a build verb | ✓ | ✓ | — | missing |

`lib/porthole_cmd_brief.py` is the one that matters: §9 tells every agent to
run `porthole brief --json` first, and it carried six of ten. **Which rules an
agent followed depended on which file it happened to open** — the mechanism
§4.6 blames for the serial, caught in the act. `brief` now serves the manifest:
the human output keeps the short session set, because "a wall of text gets
skimmed" was a correct judgement, and `--json` serves all 27 with their levels
and enforcers, because a machine does not skim.

`AGENTS.md` §1 and the skill's "Non-negotiable" now carry blocks generated
between markers (`make rules`), the `gen-tools-doc.py` → `docs/TOOLS.md`
pattern this repo already runs, so drift is impossible rather than merely
detectable. `agent-protocol.md` cites ids. The narrative in all three is
untouched — that is §6's step 3, not this one.

**"Every `brain/laws/*.md` has a corresponding MUST entry" was the wrong
test**, and writing it revealed why. The laws are methodology: only three of
ten are machine-checkable, so restating them in the manifest would create nine
new SHOULDs whose text immediately competes with the notes — the exact
duplication the manifest exists to remove. `brief` already *reads* `brain/laws/`
rather than restating it, precisely because a hand-written copy once showed
three of ten and silently omitted seven, including one that cost a day. So the
test asserts what actually matters: **every law on disk reaches an agent**.

**The hook-only gap is declared, not hidden.** `test_every_must_is_enforced_in_ci_or_is_a_declared_hook_only_gap`
held a set with exactly one member — `no-trailers` — because a hook runs only
for someone who ran `git config core.hooksPath .githooks`. Naming it meant it
could not be forgotten, and meant a second one could not join it unnoticed.

That set is now empty, and it took the predicted leak to empty it. The hook did
its job on #52's commit message and the harness published the same two lines in
the pull request body, a surface neither the hook nor any test had ever read —
so the gap was not only "opt-in", it was also "message-shaped". `no-trailers`
now names three enforcers and covers both surfaces.

**One leak found while wiring it.** `porthole brief --json` published
`"ssh_target": "<user>@<ipv4>"` — the maintainer's login, in the machine-readable
output of the command §9 tells every agent to run first, and so the output most
likely to be pasted into a brain note or a PR. `SECURITY.md` names that class
itself: credential material leaking into JSON output. Redacted in `--json`,
left intact in the human output, which is read on a terminal by the person who
owns the machine.

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

**Implemented 2026-08-31**, in `tests/test_conventions.py` — six checks, all
green the day they landed, which is the argument for adding them now rather
than after the first regression.

**The stdlib-only claim in this section was wrong, at the time.** "Verified:
every import in `lib/` is stdlib" did not hold — `lib/porthole_tui/` imported
`textual` and `rich`. That was not a defect, it was the optional console
extra: CI installed textual for one job, and the matrix jobs ran the same
files without it, where they skipped. So the rule was `lib/` is stdlib-only
**outside `lib/porthole_tui/`**, enforced in both directions. **The console
was deleted afterward** (it was not worth maintaining, and the CLI experience
was where the effort belonged); the carve-out went with it, and `lib/` is now
stdlib-only with no exception at all.

**The exit-code test found a defect on its first run.** `porthole_cli.py`
returned a bare `130` for `KeyboardInterrupt` and the table in §6 of
`AGENTS.md` did not mention 130 at all. A code no caller can interpret is
exactly what `brain/laws/exit-codes-are-an-api.md` is about. It is `EX_INTERRUPT`
now and the table has the row; the test asserts the declared constants and the
documented table are the same set, in both directions.

**File hygiene cost one line.** `.editorconfig` has demanded LF, a final
newline and no trailing whitespace since the beginning and nothing had ever
checked it; the tree was compliant bar a single trailing space in
`tools/ph-firstpaint.sh`. The test then immediately caught its own author —
the rules generator was emitting markdown's two-space hard break, which is
trailing whitespace. That is what a check landing while it is green buys you.

**`tk_*` is pinned as a literal frozen set**, and `ph_*` is required for new
helpers, so removing a compatibility name is a visible line in a diff rather
than a silent break for a tool outside this repo.

Left as SHOULD, and honestly: 80 columns (calling it a MUST means reflowing
~760 lines for no benefit), one logical change per commit, the pure-function
habit, and comments that record the incident. None is machine-decidable, and
`lib/porthole_rules.py` says so rather than pretending.

## 5. Anything currently unsafe

- **The serial is public.** `477881f…` and `d1dab0e…` still resolve on GitHub
  by SHA. Branches were rewritten and one was deleted; that removes the
  reachable copies and nothing else. If it matters, it needs GitHub Support.
- **`redfin-prep` was force-pushed.** Alessandro Ianne has been told, on PR #4,
  to `git reset --hard origin/redfin-prep` before pushing there again. Until he
  does, his local clone still holds the serial and will re-publish it on his
  next push.
- **The hook is opt-in.** Any clone that has not run
  `git config core.hooksPath .githooks` runs no hook at all. For secrets that
  no longer matters: `tests/test_secrets.py` applies the same rules in CI
  whatever the local config says. The trailer ban still depends on every
  contributor having run one command nobody checks, which is why it is now the
  first item in §6.
- A root `CONTRIBUTING.md` is absent from git. `docs/CONTRIBUTING.md` is not,
  and `porthole brief` names it as the contributor entrypoint, so a clone is
  not the blank slate this section first claimed.

## 6. The next concrete step

§4.5's sensitive list is decided and **built** (2026-08-31): `logs/` is
ignored, `lib/porthole_secrets.py` and `tests/test_secrets.py` exist and reach
CI through `make test`'s glob, both hooks are wired, and the three leaks the
scanner found on its first run are redacted. The hole this session opened is
closed.

What is left, in order, because each step's enforcer is what makes the previous
step's rule real:

§4.6 and the checkable half of §4.7 are **done** (2026-08-31):
`lib/porthole_rules.py` is the manifest, `tests/test_rules.py` makes MUST mean
something, `tests/test_conventions.py` holds the six conventions a test can
decide, and the four drifting copies of the agent rules are one source plus
generated views.

What is left:

1. ~~A CI check that the trailer ban holds regardless of local hook config.~~
   Done: `lib/porthole_trailers.py`, `tests/test_trailers.py` and the
   `trailers` job in `ci.yml`. `HOOK_ONLY` is empty. The check that finally
   forced it covered a surface this list never mentioned — the pull request
   body — which is the lesson worth keeping: an enforcer covers the surfaces
   it reads, and "the rule is enforced" is only ever true per surface.
2. `AGENTS.md` shrunk: the generated block is authoritative now, so the ten
   narrative subsections under §1 can lose their restatements and keep their
   reasoning. `docs/CONTRIBUTING.md` updated to point at the manifest.
3. Branch-name advisory and the remaining §4.3 review-comment rules.
4. The SHOULDs that could become MUSTs if someone writes the check: a mutex
   rule covering tools rather than just their headers, and a `no-hardcoded-values`
   check that looks for more than the gadget IP.

Step 1 closes the last opt-in-hook hole. The rest is narrowing what SHOULD
means, which is a better problem than the one this document opened with.

**One caution for whoever implements this.** Do not let the manifest become a
second place where rules are written, drifting from the first. The test in
§4.6 forbidding duplicated-but-differently-worded rules is not bureaucratic
tidiness: `AGENTS.md` §5 and `brain/workflow/commit-conventions.md` already
disagree about upstream sign-offs today, and that disagreement is exactly how a
contributor picks whichever reading suits them.
