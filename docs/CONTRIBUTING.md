# Contributing

## Adding a tool

1. Put it in `tools/` if it is generic, `profiles/<codename>/tools/` if it
   encodes a vendor protocol or one silicon block. **Scope it honestly.**
2. Give it the standard header (see `docs/ARCHITECTURE.md`). `porthole doctor
   --tools` checks the `scope:` line.
3. Never hardcode an IP, username, slot letter or package name. Shell:
   `. tools/tk-lib.sh`. Python: `import porthole`.
4. If it deliberately induces a reset, put a timeout on every ssh — otherwise it
   wedges the device lock for everyone else.
5. Non-trivial logic leaves one runnable check behind.

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

A note earns its place if it would have saved someone a session.

- One idea per note. If the title needs an "and", it is two notes.
- Cite the evidence. A trap without a source is folklore.
- Describe the **symptom**, not just the cause. People search by symptom.
- Prefer `scope: generic`, honestly. If it only applied to one device, scope it
  there.
- Link with `[[note-id]]`.
- `porthole brain --reindex` afterwards.

## Tests

All of these run with no device attached:

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

Author and committer are the human. `Signed-off-by:` on every commit. One
logical change per commit, and the body explains **why**.

A cherry-picked commit keeps its original author — `git cherry-pick -x`. On a
community port a lot of the early device tree is someone else's work, and
getting this wrong is both rude and a licensing problem.
