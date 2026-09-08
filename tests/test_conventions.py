#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The coding conventions that a test can actually decide.

docs/HANDOFF-contribution-rules.md section 4.7 lists what this codebase does
consistently and had never written down. This file takes the half that is
machine-checkable; the other half stays a SHOULD in lib/porthole_rules.py,
because calling an unenforceable rule a MUST is how a rule set loses its
authority -- once one MUST is decorative, they all read as decorative.

The precedent is tests/test_cli_rules.py, which enforces eighteen CLI
conventions and is the working proof of the whole argument: conventions
written as tests are followed, conventions written as prose are not.

Every rule here is green today. That is deliberate and it is the whole reason
to add them now -- a check that holds the day it lands catches the first
regression, while the same check added afterwards is an argument instead.
"""
import ast
import contextlib
import io
import os
import pathlib
import re
import sys
import sysconfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import _runner                                              # noqa: E402
import porthole_secrets as secrets                          # noqa: E402

# tk_* is a compatibility surface for tools outside this repo. Frozen means
# frozen: tk_wait_fastboot has no callers in this tree and stays anyway. New
# helpers are ph_*. Removing a name here must be a visible line in a diff.
FROZEN_TK = frozenset("""
    tk_boot_id tk_deadline_ms tk_device_state tk_expired tk_have_python
    tk_in_fastboot tk_in_initramfs tk_now_ms tk_pkill tk_rearm_and_boot
    tk_reboot_escalate tk_request_bootloader tk_request_reboot
    tk_request_sysrq_reboot tk_run tk_since tk_uptime tk_wait_fastboot
    tk_wait_ssh
""".split())

# Stdlib above the 3.8 floor, so absent from a floor interpreter's own set.
# Importing one is fine, but only guarded with a fallback -- see
# porthole_dtsdelta.py, which try/excepts tomllib and hand-rolls the reader
# below it. That is also why this manifest is Python and not TOML: on the
# floor, a TOML rule file means shipping a parser to read the file that
# forbids shipping parsers.
LATER_STDLIB = frozenset({"tomllib", "zoneinfo", "graphlib"})

TEXT_SUFFIXES = (".py", ".sh", ".md", ".yml", ".yaml", ".env", ".conf", ".toml")


def _tracked():
    """Shared with the secrets scanner, which already handles the case that
    broke this first: tests/ci-local.sh runs the suite from a `git archive`
    extraction, where there is no .git and `git ls-files` says nothing at all."""
    return [ROOT / rel for rel in secrets.tracked(ROOT)]


def _stdlib_names():
    """`sys.stdlib_module_names` is 3.10+, and the declared floor is 3.8.

    The first version of this fell back to an empty set there, so on the oldest
    interpreter -- the one job that exists to catch what newer ones hide -- it
    reported every import in lib/ as a third-party dependency. A check that
    inverts on the floor is worse than no check, so derive the set from the
    stdlib directory when the attribute is missing."""
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return set(names)
    found = set(sys.builtin_module_names)
    roots = [sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib")]
    for root in list(roots):
        if root:
            roots.append(os.path.join(root, "lib-dynload"))
    for root in roots:
        try:
            found.update(e.split(".")[0] for e in os.listdir(root))
        except OSError:
            continue
    return found


def _imports(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                yield n.lineno, a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            yield n.lineno, n.module.split(".")[0]


def test_lib_is_stdlib_only_with_no_exceptions():
    """`porthole` must work on a bare 3.8 with nothing installed -- that is the
    floor the CLI declares, and a dependency is the one thing that would break
    it silently on someone else's machine. The console was the one exception,
    and a rule with one exception is a rule everybody has to remember the
    shape of. Deleting it makes the rule simpler than the one it replaces."""
    stdlib = _stdlib_names() | LATER_STDLIB
    assert "json" in stdlib and "pathlib" in stdlib, (
        "the stdlib set came back wrong, so this check would flag everything")
    local = {p.stem for p in (ROOT / "lib").glob("*.py")}
    bad = []
    for path in sorted((ROOT / "lib").rglob("*.py")):
        rel = path.relative_to(ROOT)
        for lineno, mod in _imports(ast.parse(path.read_text())):
            if mod not in stdlib and mod not in local:
                bad.append(f"{rel}:{lineno}: {mod}")
    assert not bad, ("lib/ is stdlib-only, no exceptions:\n  " + "\n  ".join(bad))


def test_exit_codes_come_from_the_documented_table():
    """brain/laws/exit-codes-are-an-api.md. 69 versus 1 is the distinction that
    matters most -- "the check did not happen" is not "the check failed" -- and
    a code the table does not mention is one no caller can interpret.

    130 was returned by porthole_cli for KeyboardInterrupt and was missing from
    the table entirely, which is how this test found its first defect."""
    table = {int(m.group(1)) for m in re.finditer(
        r"^\| (\d+) \|", (ROOT / "AGENTS.md").read_text(), re.M)}
    assert table, "suspect this parser, not AGENTS.md"
    declared = {int(m.group(2)) for m in re.finditer(
        r"^(EX_[A-Z_]+) = (\d+)", (ROOT / "lib/porthole_cli.py").read_text(), re.M)}
    assert declared == table, (
        f"AGENTS.md section 6 and porthole_cli.py disagree.\n"
        f"  documented but not declared: {sorted(table - declared)}\n"
        f"  declared but not documented: {sorted(declared - table)}")


def test_no_exit_uses_a_bare_number():
    """A named constant is what makes an exit code greppable and reviewable:
    a bare number reads as magic, EX_UNAVAILABLE does not. Scoped to lib/ and
    bin/, because a test script exiting zero is not the CLI's API."""
    bad = []
    paths = sorted((ROOT / "lib").rglob("*.py")) + [ROOT / "bin/porthole"]
    for path in paths:
        for m in re.finditer(r"sys\.exit\(\s*(\d+)\s*\)", path.read_text()):
            bad.append(f"{path.relative_to(ROOT)}:{m.group(1)}")
    assert not bad, "use the EX_* constants:\n  " + "\n  ".join(bad)


def test_the_tk_helper_surface_is_frozen():
    """tk_* names are a compatibility surface for tools outside this repo, so
    they are never renamed and never deleted -- not even when unused. New
    helpers are ph_*, which is how the surface stops growing without stopping
    the library from growing."""
    text = (ROOT / "lib/porthole.sh").read_text()
    defined = set(re.findall(r"^(tk_[a-z0-9_]+)\(\)", text, re.M))
    gone = FROZEN_TK - defined
    assert not gone, (
        "these tk_* helpers are a frozen surface and were removed or renamed:\n  "
        + "\n  ".join(sorted(gone)))
    new = defined - FROZEN_TK
    assert not new, (
        "new shell helpers are ph_*, not tk_* -- tk_ is frozen:\n  "
        + "\n  ".join(sorted(new)))


def test_tracked_text_files_are_clean():
    """.editorconfig has said LF, a final newline and no trailing whitespace
    since the beginning, and nothing has ever checked it. The tree was already
    compliant bar one line, so this costs nothing and stays green."""
    bad = []
    for path in _tracked():
        if path.suffix not in TEXT_SUFFIXES or not path.exists():
            continue
        raw = path.read_bytes()
        if not raw:
            continue
        rel = path.relative_to(ROOT)
        if b"\r\n" in raw:
            bad.append(f"{rel}: CRLF line endings")
        if not raw.endswith(b"\n"):
            bad.append(f"{rel}: no final newline")
        for i, line in enumerate(raw.split(b"\n"), 1):
            if line.rstrip() != line:
                bad.append(f"{rel}:{i}: trailing whitespace")
    assert not bad, "\n  ".join(bad)


# A `dict(os.environ)` that is NOT a child process's environment, and why. All
# three build the mapping `load_config` READS: nothing is spawned, and
# scrubbing there would hide a stale export from the drift guard whose whole
# job is to report it.
#
# The exemption is per FILE, which is the ceiling: a future child spawned from
# one of these three would not be caught. They were chosen because none of them
# spawns anything today -- narrow the exemption to a line number on the day one
# does.
ENV_COPIES_THAT_SPAWN_NOTHING = {
    "lib/porthole.py": "load_config's own env argument",
    "lib/porthole_cli.py": "Ctx.cfg builds that argument -- and child_env "
                           "itself lives here",
}


def test_a_child_process_environment_comes_from_child_env():
    """#63: one scrub, or one caller that forgets.

    `build_env` in porthole_cmd_build popped PMB_SUDO and was right to. It was
    also alone: `pkg`, `run`, `verify` and `tui` each built their own
    `dict(os.environ)`, so `env -u PMB_SUDO porthole build` was redundant while
    `env -u PMB_SUDO porthole pkg build` was load-bearing. Nobody could tell
    which, so agents applied the prefix to everything for eleven days and the
    variable was never removed at its source.

    A shared helper only helps while it is the only door. This is the door
    being the only door."""
    bad = []
    for path in sorted((ROOT / "lib").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ENV_COPIES_THAT_SPAWN_NOTHING:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if "dict(os.environ" in line or "os.environ.copy()" in line:
                bad.append(f"{rel}:{i}: {line.strip()}")
    assert not bad, (
        "these build a child's environment by hand, so a stale export the "
        "rest of the CLI scrubs reaches the child anyway -- use "
        "porthole_cli.child_env(), or add the file to "
        "ENV_COPIES_THAT_SPAWN_NOTHING with the reason it spawns nothing:\n  "
        + "\n  ".join(bad))


def test_the_runner_reports_a_skip_as_a_skip():
    """A suite whose subject is absent on this host must say so, not pass.
    Two suites hand-rolled their own loop for exactly this and so could not
    use the parallel runner; the runner is where the third outcome belongs."""
    import types
    sys.path.insert(0, str(ROOT / "tests"))
    import _runner

    ns = types.ModuleType("__main__")

    class Skip(Exception):
        pass

    def test_a():
        pass

    def test_b():
        raise Skip("no pmaports checkout on this host")

    ns.Skip, ns.test_a, ns.test_b = Skip, test_a, test_b
    saved = sys.modules["__main__"]
    saved_jobs = os.environ.get("PORTHOLE_TEST_JOBS")
    sys.modules["__main__"] = ns
    # Serial, so the assertions read the buffer this process redirected --
    # a forked worker writes to the real stdout, not to a StringIO the
    # parent swapped in after the fork.
    os.environ["PORTHOLE_TEST_JOBS"] = "1"
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _runner.run({"test_a": test_a, "test_b": test_b})
        out = buf.getvalue()
    finally:
        sys.modules["__main__"] = saved
        if saved_jobs is None:
            del os.environ["PORTHOLE_TEST_JOBS"]
        else:
            os.environ["PORTHOLE_TEST_JOBS"] = saved_jobs
    assert rc == 0, f"a skip is not a failure: rc={rc}\n{out}"
    assert "1/2 passed" in out, out
    assert "1 skipped" in out, out
    assert "SKIP" not in out, (
        "uppercase SKIP is reserved for 'this whole file skipped' -- a "
        "per-test skip must not wear that word:\n" + out)


def test_no_suite_hand_rolls_its_own_test_loop():
    """The concurrency has to be INSIDE the file.

    `make test` parallelises across files, so its wall clock floors at the
    slowest single file -- which was `tests/test_cli.py` at 70s. `make smoke`
    and `make floor` do not parallelise at all: both iterate the same files in
    a plain shell `for` loop. One serial `for name, fn in tests` inside a suite
    therefore costs all three passes, which is why this is a rule and not a
    preference. tests/_runner.py is the one runner.
    """
    loop = re.compile(r"^\s*for (?:name, fn|n, f) in tests:", re.M)
    bad = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if loop.search(path.read_text()):
            bad.append(path.name)
    assert not bad, (
        "these suites run their tests serially in their own loop; call "
        "`_runner.run(globals())` instead:\n  " + "\n  ".join(bad))


# The two files whose SUBJECT is an old name, which is the one honest reason
# to write one. Same shape as ENV_COPIES_THAT_SPAWN_NOTHING above: an explicit
# pair with its reason, rather than a rule loose enough to miss a real one.
NAMES_AN_OLD_NAME_ON_PURPOSE = {
    "lib/porthole_cmd_tools.py": "renamed() is what answers a request for an "
                                 "old name, and its docstring shows one",
    "tests/test_cli.py": "the test that asks for one and checks the answer",
    "tests/test_permissions.py": "the fixture for a stale allow rule IS an "
                                 "old path -- that is the thing being detected",
}


def test_no_file_names_a_tool_that_no_longer_exists():
    """The rename is only finished when nothing points at the old names.

    Deliberately NOT a ban on the string `tk-`: `tk_*` shell helpers are a
    frozen surface (see test_the_tk_helper_surface_is_frozen, which forbids
    the opposite thing) and brain notes quote historical sessions verbatim.
    What is banned is naming a FILE that is not there -- a doc whose command
    cannot be copy-pasted, or a lib that builds an argv from a path that does
    not resolve.
    """
    # The OLD prefix only. A `ph-` name that does not resolve is a different
    # thing and often a legitimate one -- a design doc naming a tool nobody has
    # written yet, prose saying a tool was deleted, a /tmp path a script
    # writes. All three are in this tree and none of them is a stale pointer.
    named = re.compile(r"\b(tk-[a-z0-9-]+\.(?:sh|py))\b")
    bad = []
    for path in _tracked():
        if path.suffix not in TEXT_SUFFIXES or not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.split("/")[0] == "brain":
            continue            # notes quote the sessions they came from
        if rel in NAMES_AN_OLD_NAME_ON_PURPOSE:
            continue
        for name in sorted(set(named.findall(path.read_text(errors="replace")))):
            if not ((ROOT / "tools" / name).exists()
                    or list(ROOT.glob("profiles/*/tools/" + name))):
                bad.append("{}: {}".format(path.relative_to(ROOT), name))
    assert not bad, (
        "these name a tool file that does not exist:\n  " + "\n  ".join(bad))


def test_no_new_tool_carries_the_old_prefix():
    """The rename is finished. `tk_*` shell FUNCTIONS are still frozen and
    still fine -- see test_the_tk_helper_surface_is_frozen, which forbids the
    opposite thing. This is about files."""
    sys.path.insert(0, str(ROOT / "lib"))
    import porthole_cmd_tools

    bad = [t.name for t in porthole_cmd_tools.collect(ROOT)
           if t.name.startswith("tk-")]
    assert not bad, (
        "tools are ph-, not tk-:\n  " + "\n  ".join(sorted(bad)))


# The knobs that keep the old name, each of which also answers to a
# PORTHOLE_* twin with the OLD name winning -- lib/porthole.py::legacy is the
# contract and lib/porthole.sh line ~180 is its shell half. A fourteenth entry
# is a regression, and this list is where the exception has to be argued for.
#
# `TK_SSH_OPTS` is kept and is NOT a knob: lib/porthole.sh builds it from
# PORTHOLE_CONNECT_TIMEOUT, PORTHOLE_SSH_PORT and PORTHOLE_SSH_KEY, so an
# input of that name would be a second authority over the array.
# `TK_DEVICE_` is not a name at all -- it is the brace-expansion fragment in
# `TK_DEVICE_{LOCK,TIMEOUT,MAX,STATE}`, which documents four kept names.
KEPT_LEGACY_KNOBS = {
    "TK_HOST", "TK_AGENT", "TK_POLL", "TK_DEVICE_STATE", "TK_FORCE",
    "TK_SSH_OPTS", "TK_PMOS_PASSWORD", "TK_DEVICE_LOCK", "TK_RUN_TIMEOUT",
    "TK_WKPHASE_OFFSETS", "TK_SCROLL_URL", "TK_LOGIN_PASSWORD",
    "TK_BOOT_DEADLINE", "TK_DEVICE_TIMEOUT", "TK_DEVICE_MAX",
    "TK_DEVICE_",
}


def test_no_new_environment_knob_carries_the_old_prefix():
    """Everything else moved to PORTHOLE_* outright, because each was read by
    one tool and an alias for a name nothing else says is dead weight."""
    found = set()
    for path in _tracked():
        if path.suffix not in (".py", ".sh") or not path.exists():
            continue
        if path.relative_to(ROOT).parts[0] == "brain":
            continue
        found |= set(re.findall(r"\bTK_[A-Z0-9_]+\b",
                                path.read_text(errors="replace")))
    extra = sorted(found - KEPT_LEGACY_KNOBS)
    assert not extra, (
        "new knobs are PORTHOLE_*; these are neither that nor on the kept "
        "list:\n  " + "\n  ".join(extra))


def test_no_suite_defines_a_test_below_its_main_block():
    """A `def test_*` under `if __name__ == "__main__": sys.exit(main())` is
    never bound when the runner sweeps `globals()`, because the sweep runs
    while that line is executing and the function below it does not exist yet.

    tests/test_buildroot.py carried one for the repo's entire history. It
    reported `19/19 passed` and held twenty tests, and no amount of running
    the suite could have said so -- the file was green, and green is exactly
    what it looks like. Found by reading, and named in
    brain/traps/a-test-that-passes-either-way-is-not-a-guard.md as shape one.

    Six lines, and the class is closed permanently.
    """
    bad = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        lines = path.read_text(errors="replace").splitlines()
        guard = next((i for i, line in enumerate(lines)
                      if line.startswith('if __name__ ==')), None)
        if guard is None:
            continue
        for i, line in enumerate(lines[guard + 1:], start=guard + 2):
            if line.startswith("def test_"):
                bad.append("{}:{}: {}".format(
                    path.relative_to(ROOT), i, line.split("(")[0]))
    assert not bad, (
        "defined below the __main__ block, so the runner never binds "
        "them:\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
