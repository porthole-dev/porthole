# Architecture

Three layers, each usable without the one above it.

```
  bin/porthole            humans, bootstrap, agents wanting JSON
       │                  (never on a tool's hot path)
       ▼
  lib/porthole.{sh,py}    config resolution, ssh transport, device state
       │                  (sourced / imported directly by every tool)
       ▼
  profiles/<device>/      device facts as data
```

## Why two libs

The toolbox is roughly half bash and half python, and a bash tool cannot import
a python module. So the resolution logic exists twice — and
`tests/test_shell_lib.sh` diffs the two on every resolved key, because two
implementations of one semantics drift apart the moment nothing compares them.

## Why not a `Device` class and a full rewrite

It was considered. It rewrites ~130 tools, 60 of them shell, and builds a device
abstraction validated against exactly one device. Months of work, most of it
speculative. The tools already read environment variables; giving them one place
to get those from is the change that was actually needed.

## Why not a mandatory `porthole run <tool>` wrapper

Single entrypoint, discoverable — and it breaks direct invocation, which is what
every document and every agent already does. Kept as an optional convenience
verb instead.

## The command registry

`bin/porthole` is a thin launcher. Every verb is one `lib/porthole_cmd_<name>.py`
exporting a `SPEC` dict, discovered by glob at startup:

```python
SPEC = {
    "verb":  "doctor",
    "help":  "one line, shown in `porthole --help`",
    "order": 20,                      # sort key in help output
    "args":  [(["--json"], {"action": "store_true", "help": "..."})],
    "run":   cmd_doctor,              # (args, ctx) -> int
    "examples": ["porthole doctor"],
}
```

Adding a verb needs no edit anywhere else — no central list, no import. That is
deliberate: a registry you must remember to update is a registry that goes
stale, and this project expects contributors who have never opened
`bin/porthole`.

`run` receives a `Ctx` carrying the checkout root, a lazily-loaded config and an
`Out` helper, so a command never rediscovers the checkout or reimplements colour
handling. Config is lazy because `version` and `completion` must work in a
checkout with no device selected.

A command module that fails to import is reported and skipped rather than taking
the CLI down: one broken third-party verb must not stop you running
`porthole doctor` to find out why.

Shell completions are generated from this same registry, so they cannot drift.

## Naming

`tk_*` shell function names are **frozen** as the compatibility surface. Every
existing script and every command line in the older documentation calls them.
New helpers are `ph_*`, so the two namespaces stay readable.

## Exit codes are an API

| code | meaning | caller should |
|---|---|---|
| 0 | success | continue |
| 1 | the thing under test failed | report it — a result, not an error |
| 64 | usage error | fix the invocation |
| 75 | could not get the device lock | **retry** |
| 76 | device in the wrong state | **do not retry** — something must move it |
| 124 | killed at the hold ceiling | a wedge; investigate |

The 75/76 split earns its keep: waiting fixes a 75 and never fixes a 76. Keep 1
for "the measurement says no", so an agent can tell a broken tool from a
negative answer.

## Tool header convention

Every tool opens with a block an agent can read with `head -20`:

```
#!/bin/bash
# scope: generic          | soc:<soc> | device:<codename>
# needs: BOOTED           | FASTBOOT | FROZEN | INITRAMFS | on-device | any | -
# env:   TK_CYCLES (default 20), PHONE, TK_AGENT
# exits: 0 all clean · 1 a cycle failed · 75 lock · 76 wrong state
# N suspend/resume cycles with per-cycle evidence.
```

All four fields are required and `tests/test_tools.py` enforces them, along with
valid scope and needs values. `porthole tools lint` lists any gaps.

`needs` values:

| value | meaning |
|---|---|
| `-` | host only; never touches the device |
| `BOOTED` | needs a booted device answering ssh |
| `FASTBOOT` | needs the bootloader |
| `FROZEN` | a recovery tool for the kernel-alive/userspace-gone state |
| `INITRAMFS` | the boot stopped in the pmOS initramfs debug shell (`tsh.py`) |
| `any` | probes state and handles more than one |
| `on-device` | runs *on* the device, pushed or installed there |

A tool touching the device must use the shared lib or the mutex. The rare
exception declares `# lib-exempt: <why>` in its own header — `stallwatch.sh`
does, because detecting the PAM stall requires a raw ssh with a fixed timeout,
which is exactly what `tk_boot_id`'s retry would mask.

## Device-scoped tools

A probe that encodes a vendor protocol or one silicon block lives in
`profiles/<codename>/tools/`, not `tools/`. `porthole run` searches the active
profile first. Over-claiming portability is worse than scoping honestly.

## Why the console has a dependency and nothing else does

The first console was stdlib `curses`, on the rule that a tool whose job is to
work on a broken host must not need anything installed. That rule is still
right, and it is why the CLI will never grow a dependency: `porthole next`,
`porthole doctor` and all 98 tools run on Python 3.8 with nothing but the
standard library.

The console is a different contract. It is the interface you drive a port
*from*, on a working laptop, and holding it to the broken-host rule cost it a
widget layer: no argument entry, no filesystem navigation, six foreground
colours, and a palette that could reach a verb's default and nothing past it.

So the rule is scoped rather than deleted. `lib/porthole_tui/` needs Python 3.10
and `textual`; `lib/porthole_tui/gate.py` is importable on 3.8 and explains why
when it cannot run. CI enforces the split: the 3.8/3.11/3.13 matrix runs with
textual absent and must stay green, so an import that leaks into the CLI fails
the build.
