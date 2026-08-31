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

# The console is an optional extra: CI installs textual for one job, and the
# matrix jobs run the same files without it, where they skip. Everything else
# in lib/ must import on a bare interpreter with nothing installed.
CONSOLE_EXTRA = "porthole_tui"

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


def test_lib_is_stdlib_only_outside_the_console_extra():
    """`porthole` must work on a bare 3.8 with nothing installed -- that is the
    floor the CLI declares, and a dependency is the one thing that would break
    it silently on someone else's machine. The rule holds perfectly today,
    which is exactly why it is worth pinning now."""
    stdlib = _stdlib_names() | LATER_STDLIB
    assert "json" in stdlib and "pathlib" in stdlib, (
        "the stdlib set came back wrong, so this check would flag everything")
    local = {p.stem for p in (ROOT / "lib").glob("*.py")} | {CONSOLE_EXTRA}
    bad = []
    for path in sorted((ROOT / "lib").rglob("*.py")):
        rel = path.relative_to(ROOT)
        if CONSOLE_EXTRA in rel.parts:
            continue
        for lineno, mod in _imports(ast.parse(path.read_text())):
            if mod not in stdlib and mod not in local:
                bad.append(f"{rel}:{lineno}: {mod}")
    assert not bad, (
        "lib/ is stdlib-only outside the console extra:\n  " + "\n  ".join(bad))


def test_the_console_extra_is_the_only_place_a_dependency_lives():
    """The other direction, so the carve-out cannot quietly widen. If textual
    ever appears outside lib/porthole_tui/, the skip-without-it contract that
    `make console` checks is broken and the matrix jobs would fail instead."""
    outside = []
    for path in sorted((ROOT / "lib").rglob("*.py")):
        rel = path.relative_to(ROOT)
        for lineno, mod in _imports(ast.parse(path.read_text())):
            if mod in ("textual", "rich") and CONSOLE_EXTRA not in rel.parts:
                outside.append(f"{rel}:{lineno}: {mod}")
    assert not outside, "\n  ".join(outside)


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


if __name__ == "__main__":
    sys.exit(_runner.run(globals()))
