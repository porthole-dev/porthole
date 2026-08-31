---
id: a-shebang-probe-is-a-subset-of-running-the-tool
title: Parsing a shebang catches less than running the tool, and pmbootstrap --version does not need a config
scope: generic
subsystem: tooling
severity: finding
confidence: proven
evidence: differential run of both implementations 2026-08-31 (five cases, table below); pmbootstrap 3.11.1 --version with an empty HOME and no config exits 0; PR porthole-dev/porthole#2
refutes: a pmbootstrap without a config file exits non-zero on every invocation; running a host tool is therefore an unsafe probe; parsing the shebang catches the same failures more cheaply
first-learned: 2026-08-31
---

**The question, and it was a good one.** `porthole doctor` reported a host tool
`ok` on the strength of `shutil.which` finding it. A console script whose
shebang names an interpreter that no longer exists passes that and dies at exec
— the ordinary pipx/pip failure, and the one that actually bit: on a NixOS host
a pip-generated shebang outlived the python it named, doctor said `ok`, and
every host build died with `bad interpreter` on pmbootstrap's first line.

**The finding is Alessandro Ianne's** (`alexanderi96`), reported and fixed in
PR #2, `doctor: probe the pmbootstrap shebang, not just its path`. The
diagnosis is correct and it is why the check changed at all.

**What did not survive checking** is the argument for *how* to probe. PR #2
parses the shebang and verifies the interpreter exists, and rejects executing
the tool with this reasoning:

> Running the binary is deliberately not the probe: a pmbootstrap without a
> config file exits non-zero on every invocation, so a healthy host that has
> not been initialised would be flagged.

Measured on pmbootstrap 3.11.1, with `HOME` and both XDG dirs pointed at an
empty directory so no config exists anywhere:

| invocation | exit |
|---|---|
| `pmbootstrap --version` | **0** |
| `pmbootstrap config` | 2 |
| `pmbootstrap status` | 1 |

So the concern is real for most subcommands and false for the one a version
probe uses. `--version` is answered by argparse before any config is loaded.
That is precisely why the flag doctor sends is per-tool rather than a constant
(`HOST_TOOLS` in `lib/porthole_cmd_doctor.py`): `ssh --version` is not a thing
either — it exits 255 with a usage block — and a uniform probe failed a working
ssh on the first host it ran on.

**And the shebang probe catches strictly less.** Both implementations run
against the same five fixtures:

| case | shebang probe | running the tool |
|---|---|---|
| dead shebang interpreter | fail | fail |
| live interpreter, broken venv (`import` of a missing module) | **ok** | fail |
| interpreter path exists but is not executable | **ok** | fail |
| healthy wrapper | ok | ok |
| ELF binary, no shebang at all | ok | ok |

Row two is the pipx failure people actually hit more often than a dead
interpreter: the venv and its python both exist, and the package's dependencies
do not. Row three is the same `os.path.isfile`-without-`X_OK` mistake that this
whole check exists to remove, reproduced inside the fix for it — `alive =
parts[0] if os.path.isfile(parts[0]) else None`.

**What shipped instead.** `_runs(path, flag)` executes the tool with the flag
that tool answers to and reports a non-zero exit or an `OSError`. It keeps the
best idea in PR #2: python surfaces a missing interpreter as `ENOENT`, which
reads as "no such file" about a file you can plainly see, so `_shebang()` reads
the first line back and names the interpreter — the actionable half, and PR #2
is where that framing came from.

`_resolve` also gained the `X_OK` check it never had. PR #2 scoped its probe to
`tool == "pmbootstrap"`, which left that hole open on the **required**
`fastboot` row, where a config naming a non-executable file rendered green.

**The lesson, and it generalises past this check.** A cheap proxy for "does this
work" is worth having only when it is a superset of the failures the expensive
answer finds. Parsing what a program *says* it will do is a proxy; running it is
the answer. This repo keeps relearning the same shape — an exit code trusted
over content, a key file trusted over a key the device accepts, a tool on PATH
trusted over a tool that runs.
