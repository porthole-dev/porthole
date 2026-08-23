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
# tk-suspend-cycle.sh — N suspend/resume cycles with per-cycle evidence
#
# scope:    generic          | soc:<soc> | device:<codename>
# needs:    BOOTED
# env:      TK_CYCLES (default 20), PHONE, TK_AGENT
# exits:    0 all clean · 1 a cycle failed · 75 lock · 76 wrong state
```

`porthole doctor --tools` enforces the `scope:` line, so the convention is
checked rather than aspirational.

## Device-scoped tools

A probe that encodes a vendor protocol or one silicon block lives in
`profiles/<codename>/tools/`, not `tools/`. `porthole run` searches the active
profile first. Over-claiming portability is worse than scoping honestly.
