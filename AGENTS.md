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

One call gives you: which device and its live state, that device's encoded traps
as prose, the rules below, the tool catalogue pointer, the laws, and suggested
next steps. It is read-only and safe at the start of every session.

Then, as needed:

```sh
porthole doctor --all              # will the toolbox work? names every fix
porthole tools --json              # the full catalogue, with each tool's contract
porthole config --json             # every resolved value and which layer set it
porthole brain --severity law      # ten notes. Read them.
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

### Never ask for host root outside the sandbox

pmbootstrap needs root; you do not need it on the host. Use
`porthole sandbox shell` (a rootless container where root maps to the user's own
uid) or the brokered `PMB_SUDO`, which confines every request to declared paths
and audits it.

If a command is refused with exit 77, that is the broker. **Do not work around
it** — report what you needed and why. Widening a security policy to make an
error go away is how the policy stops meaning anything. `docs/SANDBOX.md`.

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

**A lesson that generalises** → a note in `brain/traps/` with a `scope:` line,
citing the evidence that proved it. That is how the *next* device benefits from
what this one cost you. Then `porthole brain --reindex`.

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
| a lesson that generalises | `brain/traps/<id>.md` with `scope:` and evidence | `porthole brain --reindex` |
| a device fact | `profiles/<codename>/device.env` | — |

`make check` before you claim it works. The tool contract is enforced by
`tests/test_tools.py`, not by review diligence.

---

## 5. Commits

- Author and committer are the human. Never take credit for someone else's work;
  a cherry-picked commit keeps its author (`git cherry-pick -x`).
- `Signed-off-by:` on every commit (DCO).
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
