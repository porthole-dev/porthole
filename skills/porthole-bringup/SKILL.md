---
name: porthole-bringup
description: Use when working on a postmarketOS device port or bring-up - booting, flashing, probing, or debugging a phone running mainline Linux. Establishes the device protocol, the config layer, and the evidence discipline that stops a session testing nothing.
---

# porthole bring-up

You are working on a postmarketOS device bring-up. A mature toolbox and a
knowledge base of two ports' worth of traps already exist here. **Use them.**

## Every session starts with

```sh
porthole doctor                    # will the toolbox work? names every fix
porthole config                    # which device, and where each value came from
```

`porthole doctor` catches the failure that reads as "every tool is broken": a
fresh install has no passwordless sudo, nearly every tool calls `sudo -n`, and
`-n` does not prompt — it just fails.

## Before you form a theory, check it is not already answered

```sh
porthole brain <the thing you are about to investigate>
```

`brain/findings/` holds questions this port has already closed. A **trap** says
"do not do X"; a **finding** says "X is already answered, and here are the
theories that are now dead". Findings rank first in search results and are
listed in `porthole brief` for exactly this reason.

This is not a nicety. A finding document that explicitly refuted two theories
sat unread while both were re-derived over a day. Each finding carries a
`refutes:` line naming the ideas it kills, so **searching for the theory you are
about to pursue finds the note that already killed it.**

When you close a question yourself, write one:

```sh
porthole brain new <kebab-id> --severity finding --refutes "the theory it kills"
```

## Read before acting

- `brain/laws/` — ten notes. Not about phones. Read them once, properly.
- `brain/playbooks/00-device-protocol.md` — before touching the device.
- `brain/workflow/agent-protocol.md` — how to work here.
- `AGENTS.md` — the full front door.

`porthole brain <keyword>` searches. `--scope soc:<yours>` filters to what
applies to your device (and always includes the generic notes).

## Build in the workspace, never with host root

pmbootstrap needs root. **You do not need it on the host, and must not ask for
it.** The build environment is a persistent rootless container where you are
uid 0 inside and the user's own unprivileged uid outside:

```sh
porthole sandbox status                      # is the workspace up?
porthole sandbox up                          # build the image if needed, start it
porthole sandbox shell --command <command>   # run one command in it
porthole sandbox shell                       # a shell, if you are a human
```

`--command` works with no TTY, which is what makes this usable by an agent at
all. Inside, `pmbootstrap` uses no sudo whatsoever, because it checks
`os.getuid()` and you are already root there.

The workspace builds on its own terms: it has its **own pmbootstrap work
directory** (`~/.local/var/porthole-sandbox`), which it creates and owns.
`porthole sandbox up` writes its pmbootstrap config and it bootstraps the
chroots on first use. Your host work dir is untouched and `--host` still uses
it. The two do NOT share a kernel tree's `.output`: the uids inside a rootless
container do not line up with the host's, so a tree built in one refuses in the
other and says so, naming the ways out. Pick one environment per tree.

`porthole doctor` reports the work dir as `workspace: work dir`. If it says the
dir is owned by someone else, the fix it prints is
`podman unshare rm -rf` -- a plain `rm -rf` cannot remove a populated one,
because the chroots' files belong to uids in your subuid range.

**If the workspace is not set up, STOP and ask the person you are working
with.** The one remaining privileged step is installing podman, and it needs a
password you cannot and must not type:

> This host has no porthole workspace yet. Please run `porthole doctor`, which
> names what is missing and how to install it. I cannot do it for you, by
> design.

Do not work around it. Do not `sudo`. Do not suggest a sudo credential cache —
`brain/traps/a-long-sudo-cache-is-unlimited-root.md` is why that trap exists.

Only what the workspace mounts is reachable from inside it: the pmbootstrap
work directory, this repo, the device lock, the USB bus and a device ssh key
made for the job. The user's `~/.ssh`, `/etc` and home directory are not
present, and that is the property the whole thing exists to keep.

## The iteration ladder — let porthole pick the rung

**Run `porthole build`. It measures, then picks.** It does an incremental
`make`, looks at what actually got rebuilt, and runs the cheapest rung that
covers it — one module rebuilt means a ~40 s push, a moved `Image.gz` means a
flash. Without `--yes` it compiles and tells you which rung it would run,
touching no device.

Do NOT reason your way to a rung from the diff and then type it. That is what
this replaces, and it is where sessions lose the most wall-clock: a header edit
moves every module's CRC without looking like a config change, and a Kconfig
edit can flip a module to built-in. Both fool a reader of the diff. Neither
fools "what did make actually write".

Name a rung explicitly only to OVERRIDE the measurement — when you know
something it cannot see, such as a deliberate full reflash after a desync.

The rungs it chooses between, top one costing fifteen times the bottom:

| rung | use it when | cost |
|---|---|---|
| `porthole build mod FOO.ko foo --yes` | a driver that is a **module** — try this FIRST | ~40 s, no reboot |
| `porthole build boot --yes` | a **DTS** change | ~40 s, one `fastboot boot` |
| `porthole build boot --kernel --yes` | built-in code, **only if this device RAM-boots without modules** -- a rebuilt kernel refuses every module already on it | ~40 s, one `fastboot boot` |
| `porthole build fast --yes` | a **CONFIG** change, or anything else that moves module CRCs -- builds and flashes the **aport release**, so the change must be in the series | ~6 min, flashes boot |
| `porthole build kernel --yes` | **rootfs** contents changed, or boot/rootfs desynced | ~10 min, reflash both |

Run any rung without `--yes` and it previews rather than builds, printing this
table so you can check you are on the right one. Add `--kernel` to `boot` to
rebuild `Image.gz` as well as the dtb.

**A device that ships from an aport does not close the `mod` rung.** The kernel
on the phone may come from the aport series while your change is in the tree,
and those diverge — but with `CONFIG_MODVERSIONS=y` a mismatched module is
*refused* by the loader, loudly, not silently accepted. So try `mod` and read
the answer; it costs forty seconds. Reserve the flashing rungs for changes that
are genuinely not in a module. A module that is held (msm.ko is pinned by
fbcon) still takes the cheap path: the on-disk copy is replaced and one reboot
runs it, which is minutes rather than a full package build.

Going up a rung when you did not have to is the single most common way to turn
a twenty-minute investigation into an afternoon. Going *down* one when the
change needed the higher rung is worse: a CONFIG edit moves every module's
`module_layout` CRC, so `mod` pushes a module the running kernel will refuse.

`porthole build` gets both directions right by measuring instead of guessing,
which is why it is the default and why typing a rung by hand should be the
exception rather than the habit.

## Never sleep after a build verb

**Every rung returns when the device is back, not when it was asked to move.**
`mod` verifies via `srcversion` that the module now running is the one you just
built; `boot`, `fast` and `kernel` poll with `tk_wait_ssh` and print the
`/proc/version` that answered.

So there is nothing left to wait for. If you catch yourself writing `sleep`
after a build, the verb already did that work — and did it better, because a
fixed sleep is wrong in both directions (`brain/laws/poll-never-sleep.md`).
`TK_BOOT_DEADLINE` sets the give-up point; it is not a poll interval.

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

### When a rung is still slow

**Do not reach for `PORTHOLE_LAX_BUILD=1`.** It skips the buildroot zap, and
this section used to call that zap most of the wall clock in the flashing
rungs. Measured 2026-08-29, interleaved, on a warm buildroot: kernel package
14.96 / 15.34 / 15.25 / 14.68 s with the flag and without, device package
1.66-1.72 s either way. **No difference.** The minutes in those rungs are
`install`, `export`, the flash and the boot wait.

The flag still accepts a real hazard -- stale build state, a `_p` apk
outranking a release, each instance presenting as a mysterious wrong-kernel bug
-- for no measured gain. `brain/findings/lax-build-buys-nothing-measurable.md`.

**What cuts a rung is picking the right one**, which `porthole build` does by
measuring. Run `porthole build purge` if a stale dev package is suspected.

It does NOT speed up the compile. envkernel bakes `CCACHE_DISABLE=1` into its
own make command, so every kernel compile is uncached no matter what is
installed -- see `brain/findings/envkernel-disables-ccache.md`. Do not go
looking for a ccache setting to fix that; there isn't one on this path.

## Non-negotiable

**Never hand-roll what a tool does.** Writing `ssh ... reboot` or `sleep 60`
means you have not found the tool yet. `ls tools/` is over 90 files — list the
whole directory before concluding something does not exist.

**Every device command goes through the mutex, declaring the state it needs:**

```sh
TK_AGENT=<you> tools/tk-device.sh --need-booted <command>
```

Exit 75 = lock unavailable, retry. Exit 76 = wrong state, **do not** retry;
something must physically move the device.

**Found the device in a state you did not put it in? Say so and hand back.**

**Confirm before anything irreversible** — flashing, thermal ramps, anything
that can leave a slot unbootable.

**Never ask for host root.** Builds go through `porthole sandbox shell`.
A request for sudo, or for a longer sudo timeout, is a bug in your plan
rather than a missing permission.

**Never hardcode** an IP, username, slot letter or package name. Shell:
`. tools/tk-lib.sh`. Python: `import porthole`.

## Before reporting any result

This is where bring-ups lose time — not wrong code, but an experiment that ran,
produced a clean null, and never exercised the code under test.

1. What proves the code under test ran? Decide which number is your control
   **before** the run.
2. If this is a null: what would look different had the path never executed?
   "Nothing" means you did not run an experiment.
3. Which kernel answered? `cat /proc/version`, `cat /proc/cmdline`.
4. Can my instrument even see what I say is absent? `dmesg` can be empty about
   boot; a journal grep counts the grep that is asking; a module parameter that
   does not exist is silently ignored.

Report **observations and conclusions separately**. Conclusions turn out wrong
constantly on a bring-up; that is fine. Conclusions indistinguishable from
observations cannot be re-audited later.

## When you learn something

- A fact about this device → `profiles/<codename>/device.env`
- A lesson that generalises → a note in `brain/traps/` with a `scope:` line and
  its evidence, then `porthole brain reindex`
- **A question you have now closed** → `brain/findings/`, with a `refutes:`
  line naming the theories it kills. This is the one most often skipped and the
  one that saves the most: the next person searches for the theory, not for
  your conclusion.
- A tool you had to write → `tools/` (generic) or the profile's `tools/`
  (vendor-specific), with the standard header
