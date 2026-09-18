# AGENTS.md — read this before touching anything

You are working on a postmarketOS device bring-up. This repo contains a mature
toolbox built for exactly that, plus a knowledge base of everything two previous
ports got wrong. **Use them. Do not reinvent them.**

This file is harness-neutral. It is the front door whether you are Claude Code,
an SDK agent, an IDE assistant, or a human reading over someone's shoulder.

---

## 0. Run this first

```sh
porthole brief --compact --json    # start here
porthole doctor                    # will the toolbox work? names every fix
```

`--compact` is the agent entry point: which device and its live state, that
device's encoded **traps**, workspace readiness, config drift, the MUST rules,
the port's next milestone with its command, and what to do next. Read-only and
safe at the start of every session.

It is 7 KB where the full brief is 60 KB, and the difference is catalogue, not
content. Measured 2026-09-18: of 61228 bytes, `findings` was 40093 and `rules`
9716 -- four fifths of it. Nothing about the device was dropped to get there;
`tests/test_brief_compact.py` fails if a trap ever is.

Reach the catalogues when you need them, which is the way they were designed
to be reached:

```sh
porthole brain <the theory you are about to pursue>   # findings rank first
porthole brief --json                                 # every rule, with why and enforcer
porthole brain search --severity law                  # ten notes. Read them once, properly.
porthole tools --grep <what>                          # 150+ tools; ask, never guess
porthole config --json                                # every resolved value and its layer
```

**Builds go in the workspace, never on the host.** A rootless container where
you are uid 0 inside and the user's own unprivileged uid outside:

```sh
porthole sandbox status
porthole sandbox up
porthole sandbox shell --command <command>    # works with no TTY
```

Never ask for host root and never work around its absence — §1
`no-host-root`, and [what that one cost](docs/AGENTS-RULES.md). An
inherited `PMB_SUDO` is a leftover and is ignored: porthole strips it from
every child process, so do not set it and do not edit the host environment
over it. A deliberate host build has prerequisites: `docs/NEW-HOST.md`.

**`porthole` is a supported CLI.** Read `porthole <verb> --help`, run the verb,
check the documented result. Its help states prerequisites, where it runs,
what it writes, preview versus execute, and where the log goes. Reading the
implementation is for **changing** it, or for a failure its help cannot
explain -- not a prerequisite for using it.

Exit codes are an API — see §7.

Then, depending on what you are doing:

| you are | read |
|---|---|
| about to touch the device | `brain/playbooks/00-device-protocol.md` |
| starting a session | `brain/workflow/agent-protocol.md` |
| starting a new device | `profiles/<codename>/checklist.md` |
| working a subsystem | `brain/playbooks/`, then `porthole brain <keyword>` |
| about to report a result | `brain/laws/every-test-needs-a-positive-control.md` |

**Do not guess whether a tool exists — ask.**

```sh
porthole tools --grep suspend       # search names and summaries
porthole tools --needs BOOTED       # what can I run right now
porthole tools ph-suspend-cycle.sh  # read its contract without opening it
```

A truncated `ls` has caused exactly the mistake of concluding a tool does not
exist. `porthole tools` knows how many there are, so no number is written here
to go stale. Every tool's first 20 lines declare `scope`, `needs`, `env` and
`exits`, so `head -20 <tool>` answers the question too.

---

## 1. The rules that are not negotiable

Every rule in this repo, with its level and the thing that enforces it. A MUST
is machine-checked and `enforced by` names the check; a SHOULD is one no test
can decide, which is a statement about what is checkable and never about what
matters. `tests/test_rules.py` fails if a MUST has no live enforcer, because a
MUST with a checkbox and nothing behind it is exactly how a device serial
reached a public branch.

**This block is generated from `lib/porthole_rules.py`** — edit that and run
`make rules`, never this list.

<!-- BEGIN GENERATED RULES -->
- **Never hand-roll what a tool already does** — writing `ssh ... reboot` or `sleep 60` means you have not found the tool yet -- `porthole tools --grep <what>`
  (`no-hand-rolling` · **MUST** · enforced by `tests/test_tools.py::test_every_tool_declares_the_four_fields`)
- **Take the device mutex, declaring the state you need** — the-lock-says-who-not-what; exit 75 means retry, exit 76 means something must move the device first
  (`device-mutex` · **MUST** · enforced by `tests/test_tools.py::test_tools_that_need_a_device_mention_the_mutex_or_use_the_lib`)
- **Found the device in a state you did not put it in? Say so and hand back** — it is usually someone else's measurement in progress, not a fault, and recovering it destroys their run
  (`hand-back-a-device-you-did-not-set` · **SHOULD** · no enforcer, and so not a MUST)
- **Confirm before anything irreversible** — flashing, set_active, thermal ramps -- a bad image on the wrong slot leaves a device that will not boot and cannot be talked to
  (`confirm-before-irreversible` · **MUST** · enforced by `tests/test_cli_rules.py::test_verbs_escaping_their_scope_require_yes`)
- **Never ask for host root; builds go through `porthole sandbox`** — the sandbox grants zero standing host privilege, and a tool that escalates on the host is the one bug this design exists to prevent
  (`no-host-root` · **MUST** · enforced by `tests/test_cli.py::test_init_prints_the_sudoers_snippet_rather_than_applying_it`, `tests/test_sandbox_container.py::test_up_argv_maps_container_root_to_our_uid`)
- **Never hardcode an IP, username, slot letter or package name** — every one of them comes from the config layer; a hardcoded value is a tool that works on exactly one desk
  (`no-hardcoded-values` · **MUST** · enforced by `tests/test_tools.py::test_no_hardcoded_gadget_ip_outside_config`)
- **Put a timeout on every ssh in anything that deliberately induces a reset** — 'the device stopped answering' is the expected outcome there, and an untimed command wedges the lock against every other agent
  (`ssh-timeout-on-reset` · **SHOULD** · no enforcer, and so not a MUST)
- **Every ssh and scp in the build path passes the shared options** — without them the device key is never offered, which is why `porthole build mod` silently failed to authenticate
  (`ssh-shared-options` · **MUST** · enforced by `tests/test_tools.py::test_the_build_path_never_invokes_ssh_without_the_shared_options`)
- **Do not sleep after a build verb** — poll-never-sleep; every rung returns when the device is back, not when it was asked to move
  (`no-sleep-after-a-build-verb` · **SHOULD** · no enforcer, and so not a MUST)
- **Prove the code under test actually ran, and decide the control first** — every-test-needs-a-positive-control; a null from a path that never executed is not a refutation
  (`prove-it-ran` · **SHOULD** · no enforcer, and so not a MUST)
- **Write down anything that would have saved someone a session** — `porthole brain new <id>`, then lint, then submit. A session that learned something and wrote nothing down is unfinished
  (`contribute-what-you-learn` · **SHOULD** · no enforcer, and so not a MUST)
- **In a review, say which claims you verified by execution and which you read** — the failure mode is not rudeness, it is a confident review of code nobody ran -- and an agent is the likeliest author of one
  (`state-what-you-verified` · **SHOULD** · enforced by `.github/PULL_REQUEST_TEMPLATE.md`)
- **Never publish anything on the sensitive list** — docs/HANDOFF-contribution-rules.md section 4.5; publication is irreversible and redaction is free
  (`no-secrets` · **MUST** · enforced by `tests/test_secrets.py`, `.githooks/commit-msg`, `.githooks/pre-push`)
- **If an AI helped, disclose it with `Assisted-by:` -- never as a co-author, sign-off, session or generated-with line. Sign off only what is bound upstream** — Co-authored-by is a human-only tag and a sign-off is a DCO certificate only its author can give, so CI fails a wrong attribution on the commit message, the pull request body or the issue body, on all three surfaces. Assisted-by is disclosure, never a requirement. A sign-off is NOT required on our own pull requests: a Code-Owner review and the merge certify those, and a gate that was red on every agent branch until a human ran a tool to add the line taught people to clear it without reading
  (`attribution-trailers` · **MUST** · enforced by `.githooks/commit-msg`, `tests/test_trailers.py`, `.github/workflows/ci.yml`, `.github/workflows/issue-trailers.yml`)
- **A new brain note is reindexed in the same commit** — eight commits added a note and never ran `make brain-index`; a note missing from the index is a note nobody finds, and the index is what an agent is pointed at first
  (`brain-index-current` · **MUST** · enforced by `tests/test_brain.py::test_the_index_is_current`)
- **Open the pull request after the work is done, not partway through** — a finding written mid-session is a draft: the a540 corruption note was reversed by its own next measurement, and a body filed early describes a conclusion that no longer holds
  (`pr-after-the-work` · **SHOULD** · enforced by `.github/PULL_REQUEST_TEMPLATE.md`)
- **Point this clone at the hooks once: `git config core.hooksPath .githooks`** — git ignores in-repo hooks until told, so a fresh clone has the secret scanner and the attribution check both switched off and no way to notice; `porthole brief` says which clones do
  (`hooks-installed` · **SHOULD** · enforced by `lib/porthole_cmd_brief.py`)
- **Run `make ci`, not `make check`, before opening a pull request** — `make check` skips the smoke and python-floor jobs that CI still runs
  (`make-ci-before-pushing` · **SHOULD** · no enforcer, and so not a MUST)
- **Changing a check means showing it fail without the fix** — every-test-needs-a-positive-control; the slots fixture used a spelling no device emits and so held the parser bug in place
  (`a-check-must-fail-without-its-fix` · **SHOULD** · no enforcer, and so not a MUST)
- **Every tool declares `scope:`, `needs:`, `env:` and `exits:`** — a tool nobody can describe without reading it is a tool nobody improves
  (`tool-header-fields` · **MUST** · enforced by `tests/test_tools.py::test_every_tool_declares_the_four_fields`)
- **A device-specific probe lives in `profiles/<codename>/tools/`** — in tools/ it reads as generic, and the next porter runs it on the wrong phone
  (`device-tools-live-in-a-profile` · **MUST** · enforced by `tests/test_tools.py::test_device_scoped_tools_live_in_a_profile`)
- **`lib/` is stdlib-only, no exceptions** — the CLI must work on a bare 3.8 with nothing installed; a dependency is the one thing that would break it silently on someone else's machine
  (`stdlib-only` · **MUST** · enforced by `tests/test_conventions.py::test_lib_is_stdlib_only_with_no_exceptions`)
- **Exit codes come from the documented table and nowhere else** — exit-codes-are-an-api; 69 versus 1 is the difference between 'the check did not happen' and 'the check failed'
  (`exit-codes-are-an-api` · **MUST** · enforced by `tests/test_conventions.py::test_exit_codes_come_from_the_documented_table`)
- **Python 3.8 is the floor, and bin/porthole, the Makefile and CI agree on it** — a PEP 701 f-string compiled locally on 3.14 and broke every CI job
  (`python-floor` · **MUST** · enforced by `tests/test_tools.py::test_the_python_floor_is_declared_consistently`)
- **LF endings, a final newline, and no trailing whitespace** — .editorconfig says so and nothing checked it until now
  (`file-hygiene` · **MUST** · enforced by `tests/test_conventions.py::test_tracked_text_files_are_clean`)
- **`tk_*` shell helpers are never renamed or deleted; new ones are `ph_*`** — they are a compatibility surface for tools outside this repo, which is why `tk_wait_fastboot` stays despite having no callers
  (`frozen-tk-names` · **MUST** · enforced by `tests/test_conventions.py::test_the_tk_helper_surface_is_frozen`)
- **CI runs nothing but `make` targets that `make ci` also reaches** — three hand-kept copies of the step list is how a green `make check` kept shipping a red pipeline
  (`ci-runs-only-make-targets` · **MUST** · enforced by `tests/test_tools.py::test_ci_runs_nothing_but_make_targets_that_make_ci_also_runs`)
- **One logical change per commit, and the body says why rather than what** — not machine-decidable, and calling it a MUST would make every MUST read as decorative
  (`one-logical-change-per-commit` · **SHOULD** · no enforcer, and so not a MUST)
- **A decision that must not be wrong is a pure function** — `_classify`'s own docstring: a pure function is one that can be wrong in a test instead of on a device
  (`a-decision-that-must-not-be-wrong-is-pure` · **SHOULD** · no enforcer, and so not a MUST)
- **A comment says why the code is shaped this way and what it cost to learn** — it is why this codebase is legible cold; a comment that only restates the code should be deleted in review
  (`comments-record-the-incident` · **SHOULD** · no enforcer, and so not a MUST)
<!-- END GENERATED RULES -->


**The narrative for each rule -- what it cost to learn, and how to follow it --
is [docs/AGENTS-RULES.md](docs/AGENTS-RULES.md).** It is reference: read the rule here, read its
story there when you need it. `porthole brief --json` carries each rule's
`why` and `enforced_by` in machine-readable form.

## 2. Before you report any result

This is where most of the damage in a bring-up happens: not wrong code, but an
experiment that ran, produced a clean-looking null, and never exercised the code
under test. A whole page of these accumulated in a single taimen session.

Ask, in order:

1. **What proves the code under test actually ran?** Decide which of your
   numbers is the control *before* the run, not after.
2. **If this is a null — what would look different had the path never
   executed?** If the answer is "nothing", you did not run an experiment.
3. **Which kernel answered?** `cat /proc/version`, `cat /proc/cmdline`. A
   cmdline token you cannot read back was never applied.
4. **Is my instrument capable of seeing what I claim is absent?** `dmesg` can be
   empty about boot. A journal grep counts the grep that is asking. A module
   parameter that does not exist is silently ignored.

Then: **report what you observed and what you concluded, separately.** On a
bring-up, conclusions turn out wrong constantly — that is fine and expected.
Conclusions indistinguishable from observations are not, because they cannot be
re-audited later.

`brain/laws/` has all four of these as full notes with the incidents that
produced them.

---

## 3. Configuration

Never hardcode an IP, a username, a slot letter or a package name in a tool.
Everything resolves through the config layer:

```
built-in defaults → profiles/<device>/device.env → ~/.config/porthole/config.env
                  → $PORTHOLE_ROOT/.env → the process environment
```

In shell: `. tools/ph-lib.sh`, then use `$PHONE`, `$HOST`, `"${TK_SSH_OPTS[@]}"`.
In python: `import porthole`, then `porthole.Device()`.

`porthole config` shows every resolved value and which layer it came from, which
is the fastest way to answer "why is this talking to the wrong device".

Legacy names still win, so every command line in the older taimen docs works
unchanged: `PHONE`, `HOST`, `TK_HOST`, `FASTBOOT`, `TK_POLL`, `TK_FORCE`,
`TK_AGENT`, `TK_DEVICE_*`.

### Build passwords

`porthole build kernel` and `porthole build upgrade` require
`PORTHOLE_PMOS_PASSWORD`, the postmarketOS rootfs user password (set by
`pmbootstrap install` on the device's user account). Export it once per shell:

```sh
export PORTHOLE_PMOS_PASSWORD=<the rootfs user password>
```

The legacy `TK_PMOS_PASSWORD` is still honoured and wins if both are set --
see [Legacy names](docs/CONFIG.md#legacy-names).

It must stay an environment variable, not a flag: `porthole` passes it to the
workspace container as `-e NAME` with no value on the command line, so it never
appears in `ps` where every user on the box could read it. A flag would undo
that security. `porthole doctor` warns when it is unset. The cheaper rungs
(`mod`, `boot`, `fast`) do not need it.

---

## 4. When you learn something

**A fact about this device** → `profiles/<codename>/device.env`. The boot-retry
count, the forbidden slot, the watchdog ceiling. Each of those is a fact someone
will otherwise re-derive at 3am.

**A lesson that generalises** → a note in `brain/`. **This is part of finishing
the work, not an optional extra.** Knowledge sharing is a stated goal of this
project: a session that established something real and wrote nothing down has
left the next person to pay for it again.

```sh
porthole brain new <kebab-id> --severity trap --subsystem boot
$EDITOR brain/traps/<kebab-id>.md
porthole brain lint        # enforced in CI; a note with no evidence fails
porthole brain submit      # branch, signed commit, pull request
```

The bar, and the linter enforces most of it:

- **Would it have saved someone a session?** If not it is a note to yourself.
- **`evidence:` must be something a stranger can re-check.** A trap without a
  source is folklore, and folklore is what this corpus exists to replace.
- **One idea per note.** If the title needs an "and", it is two notes.
- **Scope honestly.** Over-claiming portability is worse than scoping narrowly.
- **Write the symptom first.** People search by what they are seeing, not by
  the cause they do not know yet.

Do not contribute a guess. `confidence: suspected` exists for a reason, but a
note you have not actually verified is worse than silence — it will be trusted.

**A tool you needed and had to write** → `tools/` if it is generic,
`profiles/<codename>/tools/` if it encodes a vendor protocol or one silicon
block. Give it the standard header. Scope it honestly — over-claiming
portability is worse than scoping narrowly.

---

## 4b. Adding to porthole itself

| you wrote | it goes in | then |
|---|---|---|
| a generic tool | `tools/` | give it the four header fields |
| a device-specific probe | `profiles/<codename>/tools/` | scope it `device:<codename>` |
| a CLI verb | `lib/porthole_cmd_<name>.py` with a `SPEC` dict | nothing — it is discovered |
| a lesson that generalises | `brain/traps/<id>.md` with `scope:` and evidence | `porthole brain reindex` |
| a device fact | `profiles/<codename>/device.env` | — |

`make ci` before you claim it works -- not `make check`, which skips the
smoke and python-floor jobs that CI will still run. Every CI job is a
make target, so green locally is green on GitHub. The tool contract is enforced
by `tests/test_tools.py`, not by review diligence.

---

## 5. Commits

- **Credit an assistant with `Assisted-by: Claude`, and only that.** Every
  commit an AI assistant helped write ends with it (`Assisted-by: LLM` on
  Linux kernel patches, the form the kernel documents). It is disclosure,
  never a requirement: a commit written without AI needs nothing.
  Never `Co-Authored-By:`, `Co-developed-by:` or `Signed-off-by:` naming an
  AI (all three are human-only), never a `Claude-Session:` line or a bare
  session URL (a private link), never a "Generated with [Claude Code]" or
  robot-emoji line. A harness that appends those by default is overridden by
  this rule. See `AI.md`.
- **Only the human signs off, and only where a sign-off means something.**
  `Signed-off-by:` is the author's Developer Certificate of Origin. An
  assistant commits without one and never adds it on anyone's behalf.

  **A pull request on this repository does not need one**, and CI no longer
  asks: only someone with write access can open one, so the certificate would
  be this project asking itself about its own work. It is certified by a
  Code-Owner review and the merge — a human reading the diff. CI still
  requires it on a pull request **from a fork**, which is the case the DCO was
  designed for, and `porthole aports` still requires it on a series bound
  **upstream**. Those two are real gates; the one that used to run on our own
  branches was red on every agent pull request until a human ran a tool to add
  the missing line, which is a gate that trains people to clear it unread.
- **The ban covers every surface you publish text on** -- the commit message,
  the pull request body, and the issue body. Each one cost an escape of its
  own: #51 and #52 published the lines in the body while the hook held the
  message, and #54 was filed as an issue carrying them. A rule holds on the
  surfaces its enforcer reads, and nowhere else.
- One pattern list, `lib/porthole_trailers.py`, serves all three: the hook
  rejects a commit message with a banned line (it never adds or strips
  anything), the `Commit check` job in CI fails a pull request whose
  body or log carries one or whose commits lack their author's sign-off, and
  the issue workflow strips banned lines from an issue body. `make trailers`
  is the commit-log half on a laptop;
  `python3 lib/porthole_trailers.py --dco origin/main..HEAD` checks your
  branch's sign-offs.
- Git ignores in-repo hooks until you point it at them: a fresh clone needs
  `git config core.hooksPath .githooks` once. `porthole brief` tells you when
  a clone has not. CI catches a banned line either way, but only after you
  have pushed it -- and a push is the irreversible step.
- Author and committer are the human. Never take credit for someone else's work;
  a cherry-picked commit keeps its author (`git cherry-pick -x`).
- One logical change per commit. The body explains **why**, not what.
- Upstream-bound commits follow that project's own AI policy on the day you
  submit, not ours: `Assisted-by: LLM` plus the human's sign-off for the Linux
  kernel, Mesa's own tags for Mesa, and nothing at all to a project that does
  not accept AI-assisted work (postmarketOS does not).
- Every device-specific systemd unit needs an OpenRC equivalent.

`brain/workflow/commit-conventions.md` and `brain/playbooks/90-upstreaming.md`
have the full rules.

---

## 6. Exit codes are an API

| code | meaning | you should |
|---|---|---|
| 0 | success | continue |
| 1 | the thing under test failed | report it — a result, not an error |
| 64 | usage error | fix the invocation |
| 69 | the tool could not run at all | not a finding — the check did not happen |
| 75 | could not get the device lock | **retry** |
| 76 | device in the wrong state | **do not retry** — something must move it |
| 124 | killed at the hold ceiling | a wedge; investigate, do not just rerun |
| 130 | interrupted (SIGINT) | a person stopped it; not a result either way |

Do not conflate 1 with the others. An agent that cannot tell "the tool broke"
from "the answer is no" reports broken tools as findings.

---

## 7. Do not give worktree isolation to work touching nested repos

A worktree of the outer repo does not contain nested repos (a kernel tree, a
pmaports checkout) at all, and every git operation against their real paths is
refused from inside it. An agent given that setup can `ls` the files and do
nothing else — it will burn a long time and return BLOCKED.

---

## 8. Every verb, generated

**The contract for any one of them is `porthole <verb> --help`**: prerequisites,
where it runs, what it writes, preview versus execute, and the log path. That
is generated from the code, so it cannot drift from what the verb does.

The table below is regenerated by `porthole docs build`, and
`tests/test_documented_commands.py` fails if it is stale -- a hand-kept
inventory is wrong the first time a verb is renamed, and an agent that trusts
a wrong one wastes a session finding out.

<!-- BEGIN GENERATED: verbs -->
| verb | does | json | writes outside its profile |
|---|---|---|---|
| `init` | set this host up: identity, address, build tier, pmaports, repo | yes | needs --yes |
| `use` | switch the active device profile, and its working repo | yes | no |
| `cd` | print a path to cd into: workdir, kernel, pmaports, profile | no | no |
| `next` | where am I in this port, and what is the one next thing | yes | no |
| `brief` | everything an agent needs to start a session, in one call | yes | no |
| `slots` | read A/B slot policy from the device, never guess it | yes | no |
| `statusline` | render the build bar for an agent's status line, or install it | yes | no |
| `matrix` | what works on this device, tested separately from what exists | yes | no |
| `permissions` | grant an agent the commands a bring-up runs all day | yes | no |
| `doctor` | check the host, the profile and the device; name every fix | yes | no |
| `pkg` | find, fork and build a userspace aport, with a real progress bar | yes | needs --yes |
| `sandbox` | run pmbootstrap without handing the host to an agent | yes | no |
| `verify` | every check that runs with no device attached | yes | no |
| `tools` | search the toolbox and read a tool's contract | yes | no |
| `soc` | find devices sharing your SoC and inherit their working values | yes | no |
| `dts` | write and check a device tree without starting from blank | yes | no |
| `blobs` | get at vendor firmware during bring-up, without root | yes | no |
| `config` | print the resolved config and where each value came from | yes | no |
| `serial` | UART console: the channel that works before anything else does | yes | no |
| `kconfig` | catch the kernel symbols olddefconfig silently dropped | yes | no |
| `build` | build the kernel and package it, through envkernel | yes | needs --yes |
| `flash` | flash the built boot image, honouring the slot policy | yes | needs --yes |
| `log` | list, follow and rotate the build logs .run/ has been accumulating | yes | no |
| `disk` | report the disk two divergent pmbootstrap work dirs are spending, and what is prunable | yes | needs --yes |
| `devices` | list device profiles | yes | no |
| `sync` | move the three repos between hosts: report, push, or fast-forward | yes | needs --yes |
| `aports` | work on pmaports: status, feature branches, diffs, patches | yes | needs --yes |
| `channel` | see and switch the postmarketOS release channel | yes | no |
| `experiment` | run something with the device state captured either side | yes | no |
| `ui` | see and switch the compositor / desktop | yes | no |
| `push` | install a helper on the device where it survives a reboot | yes | no |
| `brain` | search the second brain | yes | no |
| `run` | run a tool with the config applied | no | no |
| `new-device` | scaffold a profile for a device nobody has ported yet | yes | no |
| `completion` | emit a shell completion script (bash, zsh, fish) | no | no |
| `docs` | generate the documentation site | yes | no |
| `version` | version, environment and host tool versions | yes | no |
<!-- END GENERATED: verbs -->

## 9. The session contract

If you read nothing else in this file, read this.

**Start.** `porthole brief --json`. One call: the device, its live state, the
port's progress, the single next milestone, the rules, and this device's encoded
traps. Read-only.

**Orient.** `porthole next --json` answers "where am I". Progress is DERIVED
from what is on disk, and **a probe outranks a checklist tick** — where they
disagree the tool reports `stale` and believes the probe. If you see `stale`,
that is the port claiming to be further along than it is. Fix that before
anything else.

**Run without asking:** anything read-only. Every verb marked `json: yes` in §8,
`porthole tools`, `porthole brain search`, `porthole config`, `porthole soc`,
`porthole dts` (except `new`), `porthole doctor`.

**Ask first, every time:** flashing, `set_active`, thermal ramps, anything that
writes to the device, `porthole aports` with `--yes`, and any command a
milestone does not mark `safe`. The rule is not "be careful" — it is that a bad
image on the wrong slot leaves a device that will not boot and cannot be talked
to.

**Never:** invent a value you could measure, report a result from a path you did
not prove executed, or leave a device in a state you did not find it in.

**Done means:** the thing works AND the evidence is in the transcript AND
anything that would have saved you a session is written down — `porthole brain
new <id>`, then `porthole brain lint`, then `porthole brain reindex` and commit
the index with the note. A session that learned something and wrote nothing
down is unfinished; a note that is not in the index is a note nobody finds.

**File the pull request last.** Not partway through, not as a marker that the
work started. A finding written mid-session is a draft, and this port has
already reversed one on its own next measurement — the a540 corruption note.
Opening the PR early means the body describes a conclusion that no longer
holds, and it is the body a reviewer reads. Do the work, assess the findings,
then write them up once.

**If you are lost:** `porthole next` tells you the one next action and the
command for it. That is the whole point of it existing.
