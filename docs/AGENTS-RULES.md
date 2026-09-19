# The rules, and what each one cost to learn

`AGENTS.md` section 1 is the rules themselves -- generated from
`lib/porthole_rules.py`, so they cannot drift from what the checks enforce.
This file is the narrative behind them, moved out of the front door because it
is reference rather than instruction.

Measured 2026-09-18: section 1 was 24241 bytes of a 43015-byte AGENTS.md, and
almost all of it was this. An agent reading the front door needs the rule; it
needs the story only when the rule is surprising or it is about to change one.

`porthole brief --json` carries every rule's `why` and `enforced_by` as data.

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

**Never run the redrawing form inside a tool call.** `watch` without `--json`
paints with carriage returns, and a tool call captures that into a pipe. The
human gets the smeared, column-overlapped mess it produced on 2026-09-01
rather than a bar. A repainting display has to be drawn by something the
human's terminal owns -- see the status line below.

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
porthole pkg resume webkit2gtk-6.0           # recompile IN the tree that is already there
```

**`resume` is the one that saves the afternoon.** `pmbootstrap build` runs
abuild's whole sequence and deletes `/home/pmos/build` first, so "recompile
three files and repackage" against a 5.5-hour webkit tree costs 5.5 hours --
which is why it was done by hand, twice, on 2026-09-02. `porthole pkg resume
<aport>` runs `abuild build rootpkg update_abuildrepo_index` against the
intact tree under the same lock, tracker and bar; `--apply-new-patches` puts
the aport's patches into `src/` (abuild's `prepare` is what a resume skips)
and `--pkgrel N` bumps the aport AND the build tree's copy, which is the pair
that has to move together.

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
a person.

**The fix is a status line, not a command you ask the human to type.**
`porthole statusline` renders `.run/build-status.json` -- the same bar `watch`
draws, from the same renderer -- into the agent's own status row, on a
2-second timer that keeps ticking while you are idle. It costs no tokens and
needs nothing from the human. This repo is wired up in `.claude/settings.json`;
any other project (a device workdir, say) takes one command, once:

    porthole statusline --install

So the whole obligation reduces to: **start the build detached and stop
talking about progress.**

    porthole build fast --yes --detach
    porthole pkg build <pkg> --detach

On a harness with no status line, fall back to the old contract: put
`porthole build watch` (or `pkg watch`) in your reply as a command the human
can run in their own terminal, every time, not only when they ask.

Either way, do not poll the status yourself in a loop: that spends a request
per check to re-read a number that moved 1%. The human's display is not your
display.

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
directions — see `brain/laws/poll-never-sleep.md`. `PORTHOLE_BOOT_DEADLINE`
(or the legacy `TK_BOOT_DEADLINE`) is the give-up point, not a poll interval
— default 300s. Hitting it after a flash exits 124, not 1: the write already
succeeded by the time `_ph_wait_up` runs, so a slow phone is a timeout to
raise the deadline for, not a failed flash.

### Every command that touches the device goes through the mutex

```sh
TK_AGENT=<yourname> tools/ph-device.sh --need-booted <command>
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

