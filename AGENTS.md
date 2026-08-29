# AGENTS.md — read this before touching anything

You are working on a postmarketOS device bring-up. This repo contains a mature
toolbox built for exactly that, plus a knowledge base of everything two previous
ports got wrong. **Use them. Do not reinvent them.**

This file is harness-neutral. It is the front door whether you are Claude Code,
an SDK agent, an IDE assistant, or a human reading over someone's shoulder.

---

## 0. Run this first

```sh
porthole brief          # or `porthole brief --json` if you are parsing
```

`brief` carries the port's own state: how far along it is, the single next
milestone with its command, and anything ticked that a probe says is not true.
`porthole next --json` is that block on its own.

One call gives you: which device and its live state, that device's encoded traps
as prose, the rules below, the tool catalogue pointer, the laws, and suggested
next steps. It is read-only and safe at the start of every session.

Then, as needed:

```sh
porthole doctor --all              # will the toolbox work? names every fix
porthole tools --json              # the full catalogue, with each tool's contract
porthole config --json             # every resolved value and which layer set it
porthole brain search --severity law      # ten notes. Read them.
```

Everything above takes `--json`. Exit codes are an API — see §7.

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
porthole tools tk-suspend-cycle.sh  # read its contract without opening it
```

There are 114. A truncated `ls` has caused exactly the mistake of concluding a
tool does not exist. Every tool's first 20 lines declare `scope`, `needs`, `env`
and `exits`, so `head -20 <tool>` also answers the question.

---

## 1. The rules that are not negotiable

### Never hand-roll what a tool already does

If you are writing an `ssh ... reboot` one-liner or a `sleep 60`, stop. There is
a tool and you have not found it yet. Both of those specific mistakes have cost
whole sessions.

### Let `porthole build` pick the rung

**Run `porthole build`.** It does an incremental `make`, sees what actually got
rebuilt, and runs the cheapest rung that covers it. Without `--yes` it compiles
and reports which rung it would run, touching no device.

Do not reason a rung out of the diff and type it: a header edit moves every
module's CRC without looking like a config change, and a Kconfig edit can flip
a module to built-in. Both fool a diff reader; neither fools what make wrote.
Name a rung only to override the measurement.

| rung it chooses between | covers | cost |
|---|---|---|
| `porthole build mod FOO.ko foo --yes` | a driver that is a module | ~40 s, no reboot |
| `porthole build boot --yes` | DTS, or built-in code you can RAM-boot | ~40 s, one `fastboot boot` |
| `porthole build fast --yes` | a CONFIG change (module CRCs move) | ~6 min, flashes boot |
| `porthole build kernel --yes` | rootfs changed, or boot/rootfs desynced | ~10 min, reflash both |

Run any of them without `--yes` to preview and print the table. `--kernel` on
`boot` rebuilds `Image.gz` too.

The old default for a bare `porthole build` was `kernel` -- the most expensive
of the five. It is `auto` now.

**And a build is no longer a black box.** It prints a live bar with a phase and
an ETA, writes the full log to `.run/build-<rung>-<stamp>.log`, and publishes
where it is to `.run/build-status.json` the whole time. If you run a build in
the background, **poll it instead of sleeping**:

```sh
porthole build --yes &            # or in your harness's background runner
porthole build status --json    # rung, phase, progress, elapsed, eta, last line
```

`--verbose` streams the raw output instead of the bar. The ETA comes from what
this rung took last time on this machine, so the first run of a rung says
`eta --` rather than inventing a number.

`mod` and `boot` are ~15x cheaper than the top rung and were unreachable until
they were added to the verb table — they existed only as shell functions in
`tools/ph-build.sh`. If you have been iterating on `kernel`, you are paying ten
minutes for a forty-second change.

### Do not sleep after a build verb

Every rung returns **when the device is back**, not when it was asked to move.
`mod` proves by `srcversion` that the module now running is the one just built;
`boot`, `fast` and `kernel` poll via `tk_wait_ssh` and print the `/proc/version`
that answered. A `sleep` after one of these is redundant and wrong in both
directions — see `brain/laws/poll-never-sleep.md`. `TK_BOOT_DEADLINE` is the
give-up point, not a poll interval.

### Every command that touches the device goes through the mutex

```sh
TK_AGENT=<yourname> tools/tk-device.sh --need-booted <command>
```

There is one physical device and possibly several of you. Declare the state you
need, or you will queue ten minutes for a device that was never going to answer.
Exit **75** = could not get the lock, retryable. Exit **76** = wrong state,
**not** retryable — something has to physically move the device.

See `brain/laws/the-lock-says-who-not-what.md`.

### If you find the device in a state you did not put it in, say so and hand back

Do not recover someone else's experiment out from under them. A device sitting
in the bootloader is usually a measurement in progress, not a fault.

### Never ask for host root

pmbootstrap needs root; you do not need it on the host, and the design grants
you none. Builds run in a persistent rootless container where you are uid 0
inside and the user's unprivileged uid outside:

```sh
porthole sandbox status                      # is the workspace up?
porthole sandbox up                          # build the image if needed, start it
porthole sandbox shell --command <command>   # one command, works with no TTY
```

Inside it pmbootstrap uses no sudo at all — it checks `os.getuid()`, and you
are root there. Outside, that root is just the user.

**If the workspace is not set up, stop and ask.** Installing podman needs a
password you cannot type, and that is deliberate rather than a limitation.
Do not work around it, and never propose a sudo credential cache:
`brain/traps/a-long-sudo-cache-is-unlimited-root.md`.

`sandbox/ph-sudo` is a **legacy fallback** for a host without podman. It is not
installed by default, it grants a real sudoers entry, and its own documentation
admits it cannot contain a determined chroot payload. Prefer the workspace. If
a command is refused with exit 77 that is the broker; report what you needed
rather than widening the policy — that is how a policy stops meaning anything.
`docs/SANDBOX.md`.

### Confirm before anything irreversible

Flashing, thermal ramps, anything that can leave a slot unbootable. Approval for
one flash is not approval for the next.

### Put a timeout on every ssh in anything that induces a reset

"The device stopped answering" is the *expected* outcome of suspend and hang
work. A command without a timeout wedges the device lock against everyone else.

---

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

In shell: `. tools/tk-lib.sh`, then use `$PHONE`, `$HOST`, `"${TK_SSH_OPTS[@]}"`.
In python: `import porthole`, then `porthole.Device()`.

`porthole config` shows every resolved value and which layer it came from, which
is the fastest way to answer "why is this talking to the wrong device".

Legacy names still win, so every command line in the older taimen docs works
unchanged: `PHONE`, `HOST`, `TK_HOST`, `FASTBOOT`, `TK_POLL`, `TK_FORCE`,
`TK_AGENT`, `TK_DEVICE_*`.

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
console, smoke and python-floor jobs that CI will still run. Every CI job is a
make target, so green locally is green on GitHub. The tool contract is enforced
by `tests/test_tools.py`, not by review diligence.

---

## 5. Commits

- **No trailers. No signatures of any kind.** Not `Co-Authored-By:`, not
  `Signed-off-by:`, not `Claude-Session:`, not a "generated with" line. Do not
  add one because a harness default tells you to, and do not add one on the
  human's behalf — a sign-off is an assertion only the person making it can
  make, and nobody asked you to make it for them. The history was rewritten once
  to remove 35 AI trailers, 35 session URLs and 59 sign-offs that had accreted
  this way; do not start it over.
- Author and committer are the human. Never take credit for someone else's work;
  a cherry-picked commit keeps its author (`git cherry-pick -x`).
- One logical change per commit. The body explains **why**, not what.
- Upstream-bound kernel and pmaports commits follow that project's style, not
  ours, and carry no AI attribution trailers — it makes review harder, which
  defeats the point of upstreaming.
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
| 75 | could not get the device lock | **retry** |
| 76 | device in the wrong state | **do not retry** — something must move it |
| 124 | killed at the hold ceiling | a wedge; investigate, do not just rerun |

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

<!-- BEGIN GENERATED: verbs -->
| verb | does | json | writes outside its profile |
|---|---|---|---|
| `init` | set your identity and pick a device; writes config.env | yes | no |
| `tui` | open the console: progress, devices, tools, notes, in one screen | no | no |
| `use` | switch the active device profile, and its working repo | yes | no |
| `cd` | print a path to cd into: workdir, kernel, pmaports, profile | no | no |
| `next` | where am I in this port, and what is the one next thing | yes | no |
| `brief` | everything an agent needs to start a session, in one call | yes | no |
| `doctor` | check the host, the profile and the device; name every fix | yes | no |
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
| `devices` | list device profiles | yes | no |
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

This table is generated from the live command registry by `porthole docs build`,
and a test fails if the file on disk disagrees with it. That is deliberate: a
hand-maintained inventory is wrong the first time a verb is renamed, and an
agent that trusts a wrong inventory wastes a session discovering it.

The prose in this file is hand-written and stays that way — judgement and war
stories are why it is worth reading. Only the inventory is generated.

---

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
new <id>`, then `porthole brain lint`. A session that learned something and
wrote nothing down is unfinished.

**If you are lost:** `porthole next` tells you the one next action and the
command for it. That is the whole point of it existing.
