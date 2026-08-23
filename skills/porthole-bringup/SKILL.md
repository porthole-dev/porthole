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

## Read before acting

- `brain/laws/` — ten notes. Not about phones. Read them once, properly.
- `brain/playbooks/00-device-protocol.md` — before touching the device.
- `brain/workflow/agent-protocol.md` — how to work here.
- `AGENTS.md` — the full front door.

`porthole brain <keyword>` searches. `--scope soc:<yours>` filters to what
applies to your device (and always includes the generic notes).

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
  its evidence, then `porthole brain --reindex`
- A tool you had to write → `tools/` (generic) or the profile's `tools/`
  (vendor-specific), with the standard header
