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

## The iteration ladder — pick the cheapest rung that covers your change

This is where bring-up sessions lose the most wall-clock. Four rungs, and the
top one costs fifteen times the bottom one:

| rung | use it when | cost |
|---|---|---|
| `porthole build mod FOO.ko foo --yes` | a driver that is a **module** — try this FIRST | ~40 s, no reboot |
| `porthole build boot --yes` | a **DTS** change | ~40 s, one `fastboot boot` |
| `porthole build boot --kernel --yes` | built-in code, **only if this device RAM-boots without modules** -- a rebuilt kernel refuses every module already on it | ~40 s, one `fastboot boot` |
| `porthole build fast --yes` | a **CONFIG** change, or anything else that moves module CRCs -- builds and flashes the **aport release**, so the change must be in the series | ~6 min, flashes boot |
| `porthole build kernel --yes` | **rootfs** contents changed, or boot/rootfs desynced | ~10 min, reflash both |

Run any rung without `--yes` and it previews rather than builds, printing this
table so you can check you are on the right one — `porthole build fast`, say.
Add `--kernel` to `boot` to rebuild `Image.gz` as well as the dtb.

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

## Never sleep after a build verb

**Every rung returns when the device is back, not when it was asked to move.**
`mod` verifies via `srcversion` that the module now running is the one you just
built; `boot`, `fast` and `kernel` poll with `tk_wait_ssh` and print the
`/proc/version` that answered.

So there is nothing left to wait for. If you catch yourself writing `sleep`
after a build, the verb already did that work — and did it better, because a
fixed sleep is wrong in both directions (`brain/laws/poll-never-sleep.md`).
`TK_BOOT_DEADLINE` sets the give-up point; it is not a poll interval.

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
