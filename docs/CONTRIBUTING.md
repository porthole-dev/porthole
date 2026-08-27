# Contributing

## Adding a tool

1. Put it in `tools/` if it is generic, `profiles/<codename>/tools/` if it
   encodes a vendor protocol or one silicon block. **Scope it honestly** —
   over-claiming portability is worse than scoping narrowly.
2. Give it the four-field header. All four are required and enforced:

   ```
   #!/bin/bash
   # SPDX-License-Identifier: MIT
   # scope: generic          | soc:<soc> | device:<codename>
   # needs: BOOTED           | FASTBOOT | FROZEN | INITRAMFS | on-device | any | -
   # env:   PHONE, TK_AGENT, ...          (or `-`)
   # exits: 0 ok · 1 failed · 75 lock · 76 wrong state
   # One line saying what it does.
   ```

   `porthole tools lint` lists any gaps; `make test` fails on them.
3. Never hardcode an IP, username, slot letter or package name. Shell:
   `. tools/tk-lib.sh`. Python: `import porthole`.
4. If it deliberately induces a reset, put a timeout on every ssh — otherwise it
   wedges the device lock for everyone else.
5. Non-trivial logic leaves one runnable check behind.

## Adding a CLI verb

One file. `lib/porthole_cmd_<name>.py` exporting a `SPEC` dict — see
`docs/ARCHITECTURE.md` for the shape, and any existing `porthole_cmd_*.py` for a
worked example. It is discovered at startup; there is no list to update, and
shell completion is generated from it automatically.

## Adding a device

```sh
porthole new-device <codename>
```

Fill in `device.env` as you learn. Every blank is a question you now know to
ask; `checklist.md` is the order to answer them in.

If the framework got something wrong for your device — a key that does not fit,
an assumption that does not hold — **that is the most valuable bug report this
project can get.** It has only ever been proven against one device.

## Adding to the brain

**A finding is not a trap.** A trap warns about territory ("do not do X"); a finding closes a question ("X is already answered"). Findings live in `brain/findings/`, carry a `refutes:` line naming the theories they kill, and rank above everything else in search -- because an answer that exists outranks a warning about the ground around it.

```sh
porthole brain new <id> --severity finding --refutes "the theory it kills"
```


A note earns its place if it would have saved someone a session.

- One idea per note. If the title needs an "and", it is two notes.
- Cite the evidence. A trap without a source is folklore.
- Describe the **symptom**, not just the cause. People search by symptom.
- Prefer `scope: generic`, honestly. If it only applied to one device, scope it
  there.
- Link with `[[note-id]]`.
- `porthole brain reindex` afterwards.

## Documentation

The site is generated, never hand-edited:

```sh
make docs          # regenerate site-src/ and mkdocs.yml
make docs-serve    # preview at http://127.0.0.1:8000 (needs mkdocs-material)
```

Its CLI reference comes from the command registry, its tool catalogue from the
tool headers, its profile keys from `profiles/_template/device.env`, and its
knowledge base from `brain/`. Edit those, not the site.

`site-src/`, `site/` and `mkdocs.yml` are gitignored. A committed copy would
silently shadow the generated one and let the published docs drift from the
code.

**Publishing is opt-in.** CI builds the docs with `--strict` on every push and
pull request, so a broken link fails there rather than shipping — but it only
deploys to GitHub Pages when a repository variable says to:

> Settings → Secrets and variables → Actions → Variables → `PUBLISH_DOCS` = `true`

That default exists because GitHub Pages on a private repository needs a plan
that includes it. Attempting to deploy without one puts a permanent red cross
on a workflow that is otherwise doing its job, which trains people to ignore
CI.

## Tests

All of these run with no device attached:

```sh
make ci                             # every job GitHub runs, plus the python floor
make check                          # lint + tests on your interpreter
```

`make ci` *is* CI: every job in `.github/workflows/ci.yml` runs one of these
targets and nothing else, and `tests/test_tools.py` fails if a job ever grows
its own copy of the steps or names a target `make ci` does not reach. Green here
is green on GitHub. The individual jobs, if you want one on its own:

```sh
make test       # suites, brain lint, shell lib, device mutex
make console    # the TUI suites, with textual installed (they skip without it)
make lint       # shellcheck + python syntax
make smoke      # fresh clone, bare PATH, empty HOME -- tests/ci-local.sh
make floor      # the suite on python 3.8 in a container (needs podman)
```

**Run `make ci` before you claim something passes.** `make check` compiles with
*your* interpreter, and if yours is newer than the declared floor it will accept
syntax CI rejects — a multi-line expression inside an f-string is PEP 701, legal
on 3.12+ and a syntax error below it. That exact thing compiled clean locally on
3.14 and broke every CI job.

The floor is declared in three places and a test asserts they agree:
`bin/porthole`, `PY_FLOOR` in the Makefile, and the CI matrix.

```sh
python3 tests/test_config.py        # config resolution, legacy aliases
bash    tests/test_shell_lib.sh     # the same, plus shell/python agreement
python3 tests/test_cli.py           # CLI verbs, JSON, exit codes
bash    tools/tk-device-test.sh     # the device mutex
porthole doctor --tools             # every tool has a header
```

The legacy-alias tests in the first two are the ones to be careful with. Each
row corresponds to a command line printed in real documentation; breaking one
breaks somebody's muscle memory silently.

## Commits

Author and committer are the human. One logical change per commit, and the
body explains **why**.

**Commits carry no trailers.** No `Signed-off-by:`, no `Co-Authored-By:`, no
generated-with line. If you are working through an AI assistant, that is your
business and not the log's — and an assistant must never sign off on your
behalf, because a sign-off is an assertion only you can make.

A cherry-picked commit keeps its original author — `git cherry-pick -x`. On a
community port a lot of the early device tree is someone else's work, and
getting this wrong is both rude and a licensing problem.
