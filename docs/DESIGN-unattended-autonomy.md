# Unattended device autonomy

**Date:** 2026-09-05
**Status:** approved design, not yet implemented

## The problem

An agent working on this port cannot run unattended. Five distinct things
stop it, and each has a partial answer in the toolbox that nothing ties
together.

1. **The session locks.** A blanked panel and a phosh lockscreen sit in front
   of every arm, and every "proper" API lies about the latter --
   `LockedHint`, `org.gnome.ScreenSaver.GetActive` and `lswt` all report
   unlocked while the lockscreen is plainly on screen. `tk-session.sh` solves
   this by looking at the framebuffer, and nothing calls it automatically.
2. **The phone suspends and disappears.** An unattended run that suspends is
   an unattended run that ends with a human pressing the power button.
   `tk-afk.sh` can mask suspend, with an expiry, and nothing arms it.
3. **Debug processes outlive the work.** Arms leave browsers, decoders and
   tracers running; the die sits at 80 C for hours and every thermal number
   after the first is taken on a saturated die. `tk-thermal.sh` has
   `prep/down/cool/guard` and a hardcoded three-entry `pkill` list, and
   nothing brackets an arm with it.
4. **Tools are killed by blind timeouts.** `ph_run` wraps every remote command
   in a single fixed `timeout`, so a tool that is working correctly and slowly
   is indistinguishable from one that is wedged, and the fast answer is to
   kill it.
5. **The tool surface is inconsistent.** 109 of 129 tools carry a `tk-`
   prefix naming a device the framework is no longer specific to; `exits:`
   lines say "see source"; one tool in 129 emits `--json`.

None of these is a missing tool. All of them are a missing contract.

## Constraints

- **No host root.** Builds go through the rootless workspace; nothing in this
  design runs privileged on the host.
- **Portable across init systems.** postmarketOS ships both systemd and
  OpenRC, and this project holds an OpenRC-parity convention for systemd
  fixes.
- **The device mutex must not regress.** `tk-device.sh`'s `flock` is
  per-command and kernel-released on holder death. That property is load
  bearing and is not to be traded for a longer-lived lock.
- **Probes induce nothing.** `porthole_capabilities.py` established that a
  read-only verb must be safe to run at any time and must never leave the
  phone somewhere you did not find it.
- **Report, never silently repair.** A device found in a state you did not set
  is handed back, not recovered out from under its owner.

## Verified preconditions

Measured on taimen 2026-09-05 under the device mutex, not assumed:

| fact | value |
|---|---|
| systemd | 261 (261.2) |
| cgroup hierarchy | cgroup2 unified, controllers `cpuset cpu io memory pids` |
| OpenRC on this image | absent |
| `systemd-run --user --scope --property=RuntimeMaxSec=5` | works, **no root**: scope `active` at t+2s with 1 process in its own cgroup, `inactive` at t+9s, cgroup gone |
| `--slice=porthole.slice` | created implicitly at `/user.slice/user-10000.slice/user@10000.service/porthole.slice` |
| device sudo | passwordless `sudo -n` works (`porthole doctor`) |

The TTL mechanism this design rests on is therefore demonstrated, not
inferred. The cgroup2 fallback path for non-systemd devices is *not* yet
demonstrated and carries an explicit probe task in the plan.

## Architecture

### A lease, not a wrapper

The observation that shapes everything: **an agent does not run one long
command, it makes many separate tool calls.** A bracket around a single
process cannot span an investigation. The unit is therefore a lease with a
TTL.

```
porthole session start|renew|end|status [--json]
```

Positional actions with a fixed set, and `--json` on the reporting action --
the shape `tests/test_cli_rules.py` already enforces for every verb.

`start` establishes, idempotently:

1. **Screen awake and unlocked.** `ph-screen.sh ensure` (today
   `tk-session.sh`). The PIN is read from `lib/porthole_secrets.py` and piped
   to `ph-key.py type -` on stdin, never passed as an argument -- an argv
   lands in journald. The device's PIN is currently disabled, which makes the
   swipe the whole unlock; the secrets path exists so that setting a PIN does
   not silently break unattended work.
2. **Suspend masked**, using `ph-afk.sh` (today `tk-afk.sh`) **armed with the
   lease's TTL**. Never indefinite: an indefinite mask is a phone that never
   sleeps and a battery that explains itself badly a week later.
3. **The thermal guard armed**, `ph-thermal.sh guard`, ceiling from the
   profile.
4. **`porthole.slice` created.** Every debug process from here on runs inside
   it.
5. **A device-side expiry timer armed to the TTL.** The default TTL is
   **45 minutes**, overridable with `--ttl` and by `PH_LEASE_TTL`. It is
   deliberately short: renewal is automatic on every `porthole run`, so a
   working agent never notices it, while an agent that has stopped working
   releases the phone within the hour rather than overnight.

Every `porthole run <tool>` renews the lease. That is the entire renewal
mechanism; there is no heartbeat process to supervise.

**The lease is optional.** `porthole run` is documented as "optional
convenience, never mandatory" -- tools resolve config themselves and
`tools/ph-fps.py` works when invoked directly, and always will. A tool run
outside a lease behaves exactly as it does today: it simply gets no unlock,
no suspend mask, no thermal guard and no TTL. Making the lease mandatory
would break every runbook and every interactive session for a property only
unattended work needs.

### Expiry is the safety property

On expiry, **entirely on the device, with nothing on the host involved**:

- stop `porthole.slice` -- killing every debug process and its children,
- unmask suspend,
- cool and blank,
- log the reason.

This is the answer to "the agent died mid-work": nothing on the host needed
to have survived. It is also the answer to "do not leave the phone stressed":
the ceiling on how long a phone can be held hot is a number, armed in
advance, on the device.

### What the lease deliberately does not own

- **Not the device mutex.** `ph-device.sh` (today `tk-device.sh`; renamed in
  phase 2 with everything else, behaviour untouched) keeps its `flock`. The lease owns device
  *state*; the mutex owns device *access*. Two agents may hold leases at once
  because the state each wants is identical and idempotent, so there is
  nothing to reconcile.
- **Not recovery.** `ph-recover.sh` and `ph-supervise.sh` keep their existing
  contracts and their four states (BOOTED / FROZEN / FASTBOOT / ABSENT).
  `session start` fails fast with the existing exit 76 when the device is not
  BOOTED, naming the state found. ABSENT still needs hands and is reported as
  such; this design does not pretend otherwise.
- **Not flashing.** Nothing here flashes, ever.

### Teardown mechanism, and its portability

One function in the shared library, capability-probed, with two backends:

| backend | selected when | teardown | TTL |
|---|---|---|---|
| systemd user scope | `systemd-run` present | `systemctl --user stop porthole.slice` | `RuntimeMaxSec` |
| cgroup2 direct | no systemd user manager | write to `cgroup.kill` (Linux >= 5.14) | one armed killer on the device |

The primitive is **cgroup v2**, which both init systems provide; systemd is
the convenient front end to it. Neither backend adds a dependency.

`pkill -f <pattern>` is removed from the codebase. Over ssh the pattern
matches the command line carrying it, so the tool kills its own session --
`tk-thermal.sh` already carries a comment saying this costs an afternoon, and
it cost two probe runs during the writing of this document. It is also
unportable and misses grandchildren. "Stop the slice" replaces every use.

## The run contract

### Time out on silence, not on duration

`ph_run` today is `timeout ${TK_RUN_TIMEOUT:-30} ssh ...` -- one number doing
two incompatible jobs. It becomes two:

- **`PH_SILENCE`** -- kill when the tool has produced no output and not
  updated its status for this long.
- **`PH_DEADLINE`** -- a far larger absolute backstop, exiting `124`
  (`EX_TIMEOUT`, already defined in `porthole_cli.py`).

This is the shape the ssh options in the shared library already use at the
transport layer: `ServerAliveInterval` + `ServerAliveCountMax` for liveness,
`ConnectTimeout` as the separate ceiling. Applying it one layer up is
consistency, not novelty.

### Status and logs, generalised from builds

`lib/porthole_progress.py` already writes a status snapshot
(`phase / elapsed / eta / state / pid / last`) and already has `stall_note()`,
which decides that a run has said nothing for N seconds. Generalising it from
*build* to *run* yields, for every tool:

- `.run/<tool>-<stamp>.log` -- the full output,
- `.run/<tool>-status.json` -- the snapshot,
- `porthole session status --json` -- the agent's single question.
  There is deliberately no second top-level `porthole status`: one verb,
  matching `porthole build status`, which already reads this way.

Tools opt into named phases by printing `>>` lines; `ph-build.sh` already
does and the parser already reads them. **The other 108 tools need no edit**
to gain logging and silence detection.

### The run ledger

One JSONL line appended per invocation: tool, exit code, duration, device
state, lease id. Roughly thirty lines in the runner. It exists so the *next*
audit is evidence rather than judgement -- "which tools keep failing" is
currently unanswerable because nothing records it.

## The tool contract

Four header fields are enforced today (`scope`, `needs`, `env`, `exits`).
Added, each as a test that iterates the live registry -- the pattern
`test_cli_rules.py` established, so that a rule cannot drift the day someone
adds a tool without reading the others:

- **`exits:` uses the shared codes only** (`EX_OK` 0, `EX_FAIL` 1,
  `EX_USAGE` 64, `EX_UNAVAILABLE` 69, `EX_LOCK` 75, `EX_STATE` 76,
  `EX_TIMEOUT` 124, `EX_INTERRUPT` 130). `tk-recover.sh` currently declares
  "3 see source - 4 see source - 5 see source"; "see source" is not an API,
  and `brain/laws/exit-codes-are-an-api.md` already says so. The law exists;
  nothing enforced it.
- **Modes are positional actions with a fixed set**, not boolean flags --
  the rule the CLI verbs already obey, extended to tools so the two surfaces
  stop diverging.
- **A reporting tool takes `--json`, and its output must parse** -- the same
  test the CLI verbs already pass. This applies to tools that report, not to
  all 129: bolting JSON onto a tool with nothing structured to say is
  ceremony.
- **Device-touching tools go through the mutex or the shared library**
  (already tested; restated here because the audit scores it).
- **No `pkill -f`.**

## The naming law

One prefix family, no exceptions:

| kind | form |
|---|---|
| tool files | `ph-*.sh`, `ph-*.py` |
| shell and Python functions | `ph_*` |
| per-run override env vars | `PH_*` |
| config-layer env vars | `PORTHOLE_*` (unchanged) |
| device paths | `/tmp/ph-*`, `/var/lib/ph-*`, `/var/log/ph-*`, `/usr/local/*/ph-*` |
| systemd units | `ph-*.{service,timer,scope,slice}` |

`PORTHOLE_*` and `PH_*` are kept distinct because the distinction is real and
load bearing: `PORTHOLE_HOST` is what the profile says, `PH_HOST` is what you
are overriding for this run, and `HOST=${PH_HOST:-$PORTHOLE_HOST}` is the
override chain that `porthole doctor`'s config-drift check depends on being
able to tell apart. Collapsing them would be a behaviour change wearing a
rename's clothes.

The only exemption from the law: `tk` may survive inside `brain/` where it
appears in **quoted, dated evidence**. Rewriting an observed log line would
be falsifying the record.

### Measured surface

| kind | distinct | occurrences |
|---|---|---|
| tool files `tk-*` | 109 (of 129 total) | -- |
| `TK_*` env vars | ~75 | 561 |
| `tk_*` functions | 23 | ~290 |
| device-side paths | ~30 | -- |
| units installed on the phone | `tk-lifeline.service`, `tk-lifeline.timer` | -- |
| transient unit names | `tk-load`, `tk-load-gpu`, `tk-micwatch` | -- |
| references in the taimen repo | -- | 866 |

### Migration mechanics

1. **Files:** `git mv`, so history survives.
2. **`tools/tk-lib.sh` is deleted, not renamed.** It is a symlink to
   `../lib/porthole.sh`; the real library already has the right name. Tools
   source `../lib/porthole.sh` directly. Keeping a `ph-lib.sh` alias would
   re-create the exact defect `tests/test_tools.py::test_tk_lib_is_a_symlink_to_the_shared_lib`
   was written to catch (a `sed -i` over `tools/*.sh` forks the shared library
   into a silent copy). Removing the indirection retires that bug class; the
   test is replaced by one asserting no tool sources anything but the library.
3. **Identifiers: an explicit reviewed map, never a blind sed.** One
   word-boundary-anchored replacement per identifier, each with a **count
   assertion** -- count the old name before, assert zero after and the same
   count of the new name. This is what catches the two cases a bulk sed ruins
   silently: dynamically constructed prefixes (`TK_DEVICE_` appears built at
   runtime) and substring collisions.
4. **Both repos in one change.** taimen's `tools/` *is* porthole's `tools/`;
   a rename landing in one and not the other breaks 866 references the same
   afternoon.
5. **Device-side state cannot be sed'd.** A phone with `tk-lifeline.service`
   enabled keeps running the old unit after the host is clean. `porthole
   doctor` grows one **read-only** check reporting stale `tk-*` units, paths
   and files found on the device, and names the fix. It reports; it does not
   migrate. Inducing nothing is the doctrine.
6. **The user's shell.** `TK_PMOS_PASSWORD` and friends are exported in the
   operator's environment; after the rename they would simply read as
   "unset", which is a misleading diagnosis. `porthole doctor` detects the
   *old* names still exported and says plainly that they were renamed. This
   is the **only** compatibility affordance in the change, and it lives in
   doctor rather than in the tools, so there is no second surface to maintain.
7. **Enforcement:** `tests/test_conventions.py` fails on any `\btk[-_]` or
   `\bTK_` outside the `brain/` evidence exemption. The rule stops depending
   on anyone remembering it.

## The audit

`porthole tools audit [--json]` scores every tool against the tool contract
and ranks by severity. Host-only, no device time. Because it is generated it
cannot rot, and re-running it is how the cleanup is seen to land.

It answers "which tools are rudimentary". It does **not** answer "which tools
fail on hardware" -- that is what the run ledger accumulates, and the second
audit, run against real data, is the one that answers it. Stating both
separately is deliberate: they are different failures and conflating them
would let a tool that scores perfectly and fails every time look healthy.

### Baseline, measured 2026-09-05 before any fix

The measured output of `./bin/porthole tools audit` on the day it was built:

| finding | count |
|---|---|
| `exit-code-not-shared` | 18 |
| `exit-code-undocumented` | 9 |
| `pkill-pattern` | 7 |
| `fixed-timeout` | 16 |
| `bare-sleep` | 22 |

Totals: 33 of 146 tools have findings — 34 error, 38 warning.

Phase 1 works this list down. A class is gated by `tests/test_tools.py` in the
commit that clears its last violation, never before.

Note: the `bare-sleep` figure is lower than an earlier grep estimate of 30
because sleeps inside poll loops are correctly excluded — that difference is
the checker working, not a miscount.

## Interfaces

Two consumers, one source of truth -- the status file and the ledger.

- **Agent:** `porthole session status --json`. One call answers: is a lease live,
  what is running, how hot is the die, how long is left on the TTL.
- **Developer:** the existing curses TUI grows a panel fed from the same data.

No second data path, so the two views cannot disagree.

## Testing

The lease state machine, the silence-timeout arithmetic, the contract audit,
the rename enforcement and the status parsing are pure or host-side, and are
testable with no device attached. Only the cgroup2 fallback backend needs
hardware, and it needs a probe of the shape already run for the systemd
backend.

This matters operationally: the implementation can proceed while another
agent holds the phone.

## Phases

| phase | what | why in this position |
|---|---|---|
| 0 | contract tests + `porthole tools audit` | produces the list before anything moves |
| 1 | fix or delete what the audit condemns | do not rename tools that are about to be deleted |
| 2 | the rename, both repos | mechanical, and now guarded by phase 0's tests |
| 3 | run contract: silence timeouts, status, ledger | every later piece reports through it |
| 4 | the lease, the slice and the TTL teardown | the autonomy itself |
| 5 | interfaces | last, because it is a view of phases 3 and 4 |

## Out of scope

- **A job queue or supervisor daemon.** The device mutex already serialises
  multiple agents, and a queue is a lifecycle to maintain for a problem that
  is solved.
- **Unattended flashing or recovery.** Reflashing without eyes turns a
  recoverable phone into a brick, and the one 2026-08-02 incident that needed
  hands would not have been helped by it.
- **Promoting all 129 tools to `porthole` subcommands.** The CLI grammar has
  six enforced rules and a much larger surface than this change needs.
- **Waking a phone that is ABSENT from the USB bus.** That needs a finger on
  the power button and this design says so rather than pretending.
