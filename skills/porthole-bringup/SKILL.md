---
name: porthole-bringup
description: Use when working on a postmarketOS device port or bring-up - booting, flashing, probing, or debugging a phone running mainline Linux. Establishes the device protocol, the config layer, and the evidence discipline that stops a session testing nothing.
---

# porthole bring-up

A mature toolbox and two ports' worth of encoded traps already exist here.
**`porthole` is a supported CLI: read its help, run it, check the documented
result.** Reading its source is for changing it, or for a failure its help
cannot explain -- not a prerequisite for using it.

## Start

```sh
porthole brief --compact --json   # device, state, traps, workspace, milestone
porthole doctor                   # will the toolbox work? names every fix
```

`--compact` is the entry point. The full `brief` is a 60 KB reference whose
findings and rules are catalogues -- reach those by search, below.

## Before you form a theory, search for it

```sh
porthole brain <the theory you are about to pursue>
```

Every finding carries a `refutes:` line naming the ideas it kills, so you
search for the **theory**, not for the conclusion. This is not a nicety: a
finding that explicitly refuted two theories sat unread while both were
re-derived over a day.

When you close a question yourself:

```sh
porthole brain new <kebab-id> --severity finding --refutes "the theory it kills"
```

Read once, properly: `brain/laws/` (ten notes, not about phones),
`brain/playbooks/00-device-protocol.md` before touching the device.
`AGENTS.md` is the full front door.

## Builds go in the workspace, never on the host

```sh
porthole sandbox status
porthole sandbox up
porthole sandbox shell --command <command>    # works with no TTY
```

A rootless container where you are uid 0 inside and the user's own
unprivileged uid outside, with its own pmbootstrap work dir. pmbootstrap uses
no sudo there at all.

**Never ask for host root, and never work around its absence.** If the
workspace is not set up, say so and hand back -- installing podman needs a
password you must not type:

> This host has no porthole workspace yet. Please run `porthole doctor`, which
> names what is missing. I cannot do it for you, by design.

An inherited `PMB_SUDO` is a leftover and is ignored: porthole strips it from
every child process. Do not set it, and do not edit the host environment over
it. Host builds are a deliberate choice with prerequisites -- `docs/NEW-HOST.md`.

## The build ladder -- let porthole measure

```sh
porthole build                 # prints the ladder. Runs NOTHING.
porthole build --measure       # real incremental make, then names the rung
porthole build auto --yes      # measure and run it
```

**`porthole build` with no action runs nothing** -- measuring is a real make
and takes the buildroot lock, so the command that asks "what would this do"
must not itself be a build. `--measure`, or the explicit `auto`, is what
measures.

Do NOT reason your way to a rung from the diff. A header edit moves every
module's CRC without looking like a config change, and a Kconfig edit can flip
a module to built-in. Both fool a diff reader; neither fools "what did make
actually write".

| rung | for | cost |
|---|---|---|
| `mod FOO.ko foo` | a driver that is a **module** -- try FIRST | ~40 s, no reboot |
| `boot` | a **DTS** change | ~40 s, one `fastboot boot` |
| `boot --kernel` | built-in code, only if this device RAM-boots without modules | ~40 s |
| `fast` | a **CONFIG** change, or anything moving module CRCs | ~6 min |
| `kernel` | **rootfs** changed, or boot/rootfs desynced | ~10 min |
| `image` | the whole system from pmaports; compiles no kernel tree | varies |

Name a rung by hand only to OVERRIDE the measurement. Going up a rung
needlessly turns a twenty-minute investigation into an afternoon; going down
one pushes a module the running kernel will refuse.

## Never sleep after a build verb

Every rung returns when the device is **back**, not when it was asked to move:
`mod` verifies `srcversion`, the rest poll and print the `/proc/version` that
answered. If you catch yourself writing `sleep`, the verb already did it
better (`brain/laws/poll-never-sleep.md`). `TK_BOOT_DEADLINE` is the give-up
point, not a poll interval.

Backgrounded a build? **Poll it, do not sleep:**

```sh
porthole build status --json    # rung, phase, progress, elapsed, eta, last line
```

Do not feed unchanged status or full compiler logs back through the model.

`PORTHOLE_LAX_BUILD=1` buys nothing measurable and accepts a real hazard --
`brain/findings/lax-build-buys-nothing-measurable.md`. In the workspace
porthole already passes `--lax` where it is required; you set nothing.

## Non-negotiable

<!-- BEGIN GENERATED RULES -->
- **Never hand-roll what a tool already does** — writing `ssh ... reboot` or `sleep 60` means you have not found the tool yet -- `porthole tools --grep <what>`
  (`no-hand-rolling` · **MUST**)
- **Take the device mutex, declaring the state you need** — the-lock-says-who-not-what; exit 75 means retry, exit 76 means something must move the device first
  (`device-mutex` · **MUST**)
- **Found the device in a state you did not put it in? Say so and hand back** — it is usually someone else's measurement in progress, not a fault, and recovering it destroys their run
  (`hand-back-a-device-you-did-not-set` · **SHOULD**)
- **Confirm before anything irreversible** — flashing, set_active, thermal ramps -- a bad image on the wrong slot leaves a device that will not boot and cannot be talked to
  (`confirm-before-irreversible` · **MUST**)
- **Never ask for host root; builds go through `porthole sandbox`** — the sandbox grants zero standing host privilege, and a tool that escalates on the host is the one bug this design exists to prevent
  (`no-host-root` · **MUST**)
- **Never hardcode an IP, username, slot letter or package name** — every one of them comes from the config layer; a hardcoded value is a tool that works on exactly one desk
  (`no-hardcoded-values` · **MUST**)
- **Prove the code under test actually ran, and decide the control first** — every-test-needs-a-positive-control; a null from a path that never executed is not a refutation
  (`prove-it-ran` · **SHOULD**)
- **Write down anything that would have saved someone a session** — `porthole brain new <id>`, then lint, then submit. A session that learned something and wrote nothing down is unfinished
  (`contribute-what-you-learn` · **SHOULD**)
- **If an AI helped, disclose it with `Assisted-by:` -- never as a co-author, sign-off, session or generated-with line. Sign off only what is bound upstream** — Co-authored-by is a human-only tag and a sign-off is a DCO certificate only its author can give, so CI fails a wrong attribution on the commit message, the pull request body or the issue body, on all three surfaces. Assisted-by is disclosure, never a requirement. A sign-off is NOT required on our own pull requests: a Code-Owner review and the merge certify those, and a gate that was red on every agent branch until a human ran a tool to add the line taught people to clear it without reading
  (`attribution-trailers` · **MUST**)
- **A new brain note is reindexed in the same commit** — eight commits added a note and never ran `make brain-index`; a note missing from the index is a note nobody finds, and the index is what an agent is pointed at first
  (`brain-index-current` · **MUST**)
- **Open the pull request after the work is done, not partway through** — a finding written mid-session is a draft: the a540 corruption note was reversed by its own next measurement, and a body filed early describes a conclusion that no longer holds
  (`pr-after-the-work` · **SHOULD**)
- **Point this clone at the hooks once: `git config core.hooksPath .githooks`** — git ignores in-repo hooks until told, so a fresh clone has the secret scanner and the attribution check both switched off and no way to notice; `porthole brief` says which clones do
  (`hooks-installed` · **SHOULD**)
<!-- END GENERATED RULES -->

Generated from `lib/porthole_rules.py`; the full set with enforcers is
`AGENTS.md` section 1, or `porthole brief --json`.

```sh
TK_AGENT=<you> tools/ph-device.sh --need-booted <command>   # the mutex
porthole tools --grep <what>                                # 150+ tools; ask, never guess
```

Shell tools get the config layer with `. tools/ph-lib.sh`, python with
`import porthole`.

## Before reporting any result

This is where bring-ups lose time -- not wrong code, but an experiment that
ran, produced a clean null, and never exercised the code under test.

1. What proves the code under test ran? Decide the control **before** the run.
2. If this is a null: what would look different had the path never executed?
   "Nothing" means you did not run an experiment.
3. Which kernel answered? `cat /proc/version`, `cat /proc/cmdline`.
4. Can the instrument even see what you say is absent? `dmesg` can be empty
   about boot; a journal grep counts the grep asking; a module parameter that
   does not exist is silently ignored.

Report **observations and conclusions separately**. Conclusions turn out wrong
constantly on a bring-up; that is fine. Conclusions indistinguishable from
observations cannot be re-audited later.

## When you learn something

- A fact about this device -> `profiles/<codename>/device.env`
- A lesson that generalises -> `brain/traps/`, with a `scope:` line and evidence
- **A question you have now closed** -> `brain/findings/`, with `refutes:`.
  The one most often skipped and the one that saves the most.
- A tool you had to write -> `tools/` (generic) or the profile's, standard header

Then `porthole brain reindex` in the same commit.
