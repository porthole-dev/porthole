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

Every rule in this repo, with its level and the thing that enforces it. A MUST
is machine-checked and `enforced by` names the check; a SHOULD is one no test
can decide, which is a statement about what is checkable and never about what
matters. `tests/test_rules.py` fails if a MUST has no live enforcer, because a
MUST with a checkbox and nothing behind it is exactly how a device serial
reached a public branch.

**This block is generated from `lib/porthole_rules.py`** — edit that and run
`make rules`, never this list. The sections below are the narrative: what each
rule cost to learn, and how to follow it.

<!-- BEGIN GENERATED RULES -->
- **Never hand-roll what a tool already does** — writing `ssh ... reboot` or `sleep 60` means you have not found the tool yet -- `porthole tools --grep <what>`
  (`no-hand-rolling` · **MUST** · enforced by `tests/test_tools.py::test_every_tool_declares_the_four_fields`)
- **Take the device mutex, declaring the state you need** — the-lock-says-who-not-what; exit 75 means retry, exit 76 means something must move the device first
  (`device-mutex` · **MUST** · enforced by `tests/test_tools.py::test_tools_that_need_a_device_mention_the_mutex_or_use_the_lib`)
- **Found the device in a state you did not put it in? Say so and hand back** — it is usually someone else's measurement in progress, not a fault, and recovering it destroys their run
  (`hand-back-a-device-you-did-not-set` · **SHOULD** · no enforcer, and so not a MUST)
- **Confirm before anything irreversible** — flashing, set_active, thermal ramps -- a bad image on the wrong slot leaves a device that will not boot and cannot be talked to
  (`confirm-before-irreversible` · **MUST** · enforced by `tests/test_cli_rules.py::test_verbs_escaping_their_scope_require_yes`, `tests/test_tui_safety.py::test_no_safe_milestone_command_is_irreversible`)
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
- **No attribution trailers of any kind on a commit** — they are injected by a harness default rather than typed by anyone, and the history has been rewritten twice to remove them
  (`no-trailers` · **MUST** · enforced by `.githooks/commit-msg`)
- **Run `make ci`, not `make check`, before opening a pull request** — `make check` skips the console, smoke and python-floor jobs that CI still runs
  (`make-ci-before-pushing` · **SHOULD** · no enforcer, and so not a MUST)
- **Changing a check means showing it fail without the fix** — every-test-needs-a-positive-control; the slots fixture used a spelling no device emits and so held the parser bug in place
  (`a-check-must-fail-without-its-fix` · **SHOULD** · no enforcer, and so not a MUST)
- **Every tool declares `scope:`, `needs:`, `env:` and `exits:`** — a tool nobody can describe without reading it is a tool nobody improves
  (`tool-header-fields` · **MUST** · enforced by `tests/test_tools.py::test_every_tool_declares_the_four_fields`)
- **A device-specific probe lives in `profiles/<codename>/tools/`** — in tools/ it reads as generic, and the next porter runs it on the wrong phone
  (`device-tools-live-in-a-profile` · **MUST** · enforced by `tests/test_tools.py::test_device_scoped_tools_live_in_a_profile`)
- **`lib/` is stdlib-only, except `lib/porthole_tui/` which is the optional console extra** — the CLI must work on a bare 3.8 with nothing installed; the console degrades to a skip when textual is absent
  (`stdlib-only` · **MUST** · enforced by `tests/test_conventions.py::test_lib_is_stdlib_only_outside_the_console_extra`)
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

### Never hand-roll what a tool already does

If you are writing an `ssh ... reboot` one-liner or a `sleep 60`, stop. There is
a tool and you have not found it yet. Both of those specific mistakes have cost
whole sessions.

**To watch a build, run `porthole build watch`. Do not write a poll loop.**

`porthole build watch` and `porthole pkg watch` block until the build stops
and exit with it — 0 on success, non-zero on failure or on a run whose process
has gone. `porthole build watch --json` emits one JSON object per update on
stdout, line-buffered, which is what an agent can consume; a redrawn terminal
bar is not. Start long builds with `--detach` and wait on the watcher.

Three hand-rolled poll loops were written against `--json` in a single session
and one of them timed out at ten minutes, reporting nothing, while the build
was still healthy.

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
| `porthole build boot --kernel --yes` | built-in code, only where the device RAM-boots without modules | ~40 s, one `fastboot boot` |
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

### Userspace packages: `porthole pkg`, never raw pmbootstrap

`porthole build` is the KERNEL loop. For a userspace aport -- phoc, webkit,
gst-plugins-good -- the verb is `porthole pkg`, and reaching past it to
`pmbootstrap build` is not a shortcut, it is four separate losses:

```sh
porthole pkg build webkit2gtk-6.0 --detach   # survives the session
porthole pkg watch                           # live bar, free to leave open
porthole pkg status --json                   # one-shot, for you
porthole pkg outdated                        # what you edited and did not rebuild
```

- **It takes the buildroot lock.** One workspace has one buildroot per arch and
  `abuild` wipes `$srcdir` before unpacking, so a second pmbootstrap command
  deletes the first one's source tree mid-build. This has destroyed a 37-minute
  webkit build and a full kernel build, and **both failures blamed the
  compiler.** `pmbootstrap checksum` counts -- it is what killed the kernel one.
- **It passes `--lax`,** without which a build cannot run in the workspace at all.
- **It logs, and it reports.** A raw call reports nothing for hours.
- **It verifies the artifact,** because `pmbootstrap build` can exit 0 having
  done nothing.

`porthole sandbox shell --command 'pmbootstrap build ...'` is refused for these
reasons; `--raw` overrides it if you genuinely mean to bypass all of them.

**Run it in the background and let the harness tell you it finished -- but
that puts the bar somewhere the HUMAN CANNOT SEE.** A background task's
stdout is a log file or a buffer the harness reads, not a terminal in front of
a person. `porthole pkg watch` exists precisely so the developer is never
blind to a build that is running because you decided not to poll it, and it
only works if they know to type it. So every time you start a build, you MUST
put `porthole pkg watch` in your reply to the human as a command they can run
right now -- not "if you must watch", not only when they ask, every time,
full stop. Do not poll the status yourself in a loop, though: that still
spends a request per check to re-read a number that moved 1%. Say the command
to the human; do not read it back to yourself.

The percentage is real (ninja states its total), but **the ETA is deliberately
absent during generator steps.** A `[N/M]` counter stalls dead on
single-threaded codegen -- measured at five steps in four minutes with fifteen
cores idle, which naively extrapolates to 74 hours on a healthy build. When the
line says `generating  --/s  eta --`, that is normal and the build is fine.
**Do not kill a build over a missing ETA.**

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

pmbootstrap itself refuses uid 0 outright, before it looks at anything else --
`--as-root` is the only way past, and the image wraps it so nothing has to know
that. Three more things a rootless namespace cannot do are shimmed the same
way: `mknod` (the kernel refuses device-node creation in a userns, so the
chroots get a recursive bind of the container's own `/dev`), `chmod` on a node
it does not own, and `sudo`, which has nothing to escalate to in here.
`brain/findings/what-a-rootless-workspace-cannot-do.md` has the measurements.

**The workspace has its OWN pmbootstrap work directory**
(`~/.local/var/porthole-sandbox`), created and owned by it; `porthole sandbox
up` writes the config and the chroots bootstrap on first use. The host's work
dir is untouched and `--host` still uses it. A kernel tree's `.output` belongs
to ONE of the two -- the uids do not line up -- and a build in the wrong one
refuses rather than failing inside kbuild.

**If the workspace is not set up, stop and ask.** Installing podman needs a
password you cannot type, and that is deliberate rather than a limitation.
Do not work around it, and never propose a sudo credential cache:
`brain/traps/a-long-sudo-cache-is-unlimited-root.md`.

There is **no fallback tier**. A validating privilege broker (`ph-sudo`,
`PMB_SUDO`) used to exist for a host without podman and has been removed: it
granted a real sudoers entry and could not contain a determined chroot payload,
and a weaker path that still exists is the one a stuck agent reaches for. If
you find yourself wanting host root, that is a bug in the plan.

`PMB_SUDO` is dead too. `porthole doctor` **fails** if it is still exported —
pmbootstrap invokes it directly, so a leftover kills a build with exit 78 from
deep inside pmbootstrap, naming nothing. `docs/SANDBOX.md`.

### When a rung is still slow

`PORTHOLE_LAX_BUILD=1` skips the zap, and this section used to call that zap
most of the wall clock in the flashing rungs. **Measured 2026-08-29: it is not,
and the flag buys nothing.** Interleaved runs on a warm buildroot put the
device package at 1.66-1.72 s either way. The minutes in those rungs are
`install`, `export`, the flash and the boot wait, none of which the flag
touches.

On the kernel rung it is not merely unhelpful, it is **inert**: `pmbootstrap
build --envkernel` returns from `pmb/commands/build.py` before the strict-mode
zap block, so `--lax` never reaches that path. (The kernel numbers this section
used to quote were four readings of a flag that could not have done anything.)

**In the WORKSPACE this is reversed, and porthole handles it for you.** A
non-lax `pmbootstrap build` cannot run there at all: `zap_buildroots()` umounts
the chroot, and the recursive `/dev` bind the rootless workspace needs leaves
propagated sub-mounts that cannot be umounted by path -- `umount:
/pmb/chroot_native/dev/shm: not mounted.` (exit 32) -- so the build dies at
"Zapping buildroots" before it starts. `tools/ph-build.sh` therefore passes
`--lax` automatically in the workspace and nowhere else; you do not need to set
anything. Upstream made strict the default for correctness (`e14f4169`, MR
2939, 2026-05), so it is a real trade, covered by porthole's own stale-package
guards. `brain/findings/what-a-rootless-workspace-cannot-do.md` §5.

So do not reach for it. It accepts something real -- this repo has been bitten
repeatedly by stale build state, a `_p` apk outranking a release, a stale
APKINDEX making install pick an older package, each one presenting as a
mysterious wrong-kernel bug -- for no measured gain.
`brain/findings/lax-build-buys-nothing-measurable.md`.

**What actually cuts a rung** is picking the right one, which `porthole build`
does by measuring. Run `porthole build purge` if a stale dev package is
suspected.

It does NOT speed up the compile. The compile is cached separately, and **in
the workspace that cache now works** -- the image rewrites envkernel's
`CCACHE_DISABLE=1` to a `CCACHE_DIR`, and `_ph_arm_ccache` in `tools/ph-build.sh`
installs ccache into `chroot_native` and links clang into its masquerade dir on
every activate. Measured: the same 643-step rebuild is 1m49s with no cache, 2m09s on a run
that misses everywhere, and **43 s** once the cache has seen those objects.
So it costs ~18% the first time a set of objects is compiled and pays 2.5x on
every repeat. `PORTHOLE_NO_CCACHE=1` turns it off if you build a tree once and
never again.

That helps a full rebuild -- a kernel version move, a common header, a fresh
workspace -- and does nothing for the 7-second incremental loop, which never
repeats a compilation to cache. `--host` builds use your own pmbootstrap
checkout, which is not patched and stays uncached.
`brain/findings/the-workspace-caches-kernel-compiles.md`.

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

### Build passwords

`porthole build kernel` and `porthole build upgrade` require `TK_PMOS_PASSWORD`,
the postmarketOS rootfs user password (set by `pmbootstrap install` on the
device's user account). Export it once per shell:

```sh
export TK_PMOS_PASSWORD=<the rootfs user password>
```

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
console, smoke and python-floor jobs that CI will still run. Every CI job is a
make target, so green locally is green on GitHub. The tool contract is enforced
by `tests/test_tools.py`, not by review diligence.

---

## 5. Commits

- **No trailers. No signatures of any kind.** Not `Co-Authored-By:`, not
  `Signed-off-by:`, not `Claude-Session:`, not a "generated with" line. Do not
  add one because a harness default tells you to, and do not add one on the
  human's behalf — a sign-off is an assertion only the person making it can
  make, and nobody asked you to make it for them. The history has been
  rewritten twice over this: 35 AI trailers, 35 session URLs and 59 sign-offs
  the first time, then 49, 49 and 32 the second. Do not start a third.
- Prose alone did not hold, because the lines are typed by a default rather
  than by anyone, so `.githooks/commit-msg` now strips them before they land.
  Git ignores in-repo hooks until you point it at them: a fresh clone needs
  `git config core.hooksPath .githooks` once, or the rule is back to being a
  request.
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

<!-- BEGIN GENERATED: verbs -->
| verb | does | json | writes outside its profile |
|---|---|---|---|
| `init` | set your identity and pick a device; writes config.env | yes | no |
| `tui` | open the console: progress, devices, tools, notes, in one screen | no | no |
| `use` | switch the active device profile, and its working repo | yes | no |
| `cd` | print a path to cd into: workdir, kernel, pmaports, profile | no | no |
| `next` | where am I in this port, and what is the one next thing | yes | no |
| `brief` | everything an agent needs to start a session, in one call | yes | no |
| `slots` | read A/B slot policy from the device, never guess it | yes | no |
| `matrix` | what works on this device, tested separately from what exists | yes | no |
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
