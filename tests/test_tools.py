#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The tool contract: every tool in the box must hold to it.

These are the tests that keep the toolbox improvable. A tool nobody can
describe without reading it is a tool nobody improves, and a tool with someone's
home directory baked into it is a tool that only works on one desk.

Runs with no device attached. Fast enough to be a pre-commit hook.
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

REQUIRED_FIELDS = ("scope", "needs", "env", "exits")
VALID_SCOPE = re.compile(r"^(generic|soc:[a-z0-9_-]+|device:[a-z0-9-]+)$")
VALID_NEEDS = re.compile(
    r"^(-(\s|$)|BOOTED\b|FASTBOOT\b|FROZEN\b|any\b|on-device\b)")

# Anything that would make a tool work only for its original author.
PERSONAL = re.compile(
    r"(/home/[a-z][a-z0-9_-]*|/var/home/[a-z][a-z0-9_-]*|"
    r"\b[a-z][a-z0-9_-]*@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")

# The pmOS USB-gadget address is a real default, not a personal one. It is
# legitimate in lib/ and in a profile; in a tool it should come from config.
GADGET_IP = "172.16.42.1"


def tools():
    paths = [p for p in sorted((ROOT / "tools").iterdir())
             if p.is_file() and not p.is_symlink()
             and p.name not in ("__pycache__",)]
    pdir = ROOT / "profiles"
    for profile in sorted(pdir.iterdir()) if pdir.is_dir() else []:
        tdir = profile / "tools"
        if tdir.is_dir():
            paths += [p for p in sorted(tdir.iterdir())
                      if p.is_file() and not p.name.startswith(".")]
    return paths


def head_of(path, lines=30):
    return "".join(path.read_text(errors="replace").splitlines(True)[:lines])


def field(path, name):
    m = re.search(rf"^#\s*{name}:\s*(.*)$", head_of(path), re.M)
    return m.group(1).strip() if m else None


# ------------------------------------------------------------- the contract --

def test_every_tool_declares_the_four_fields():
    """`head -20 <tool>` must answer what it does, what it needs, what it
    reads and how it exits -- without opening the file."""
    bad = []
    for path in tools():
        missing = [f for f in REQUIRED_FIELDS if field(path, f) is None]
        if missing:
            bad.append(f"{path.name}: missing {', '.join(missing)}")
    assert not bad, "tools with an incomplete header:\n  " + "\n  ".join(bad)


def test_scope_values_are_valid():
    bad = [f"{p.name}: {field(p, 'scope')!r}" for p in tools()
           if not VALID_SCOPE.match(field(p, "scope") or "")]
    assert not bad, ("scope must be generic | soc:<soc> | device:<codename>:\n  "
                     + "\n  ".join(bad))


def test_needs_values_are_valid():
    bad = [f"{p.name}: {field(p, 'needs')!r}" for p in tools()
           if not VALID_NEEDS.match(field(p, "needs") or "")]
    assert not bad, ("needs must start with - | BOOTED | FASTBOOT | FROZEN | "
                     "any | on-device:\n  " + "\n  ".join(bad))


def test_device_scoped_tools_live_in_a_profile():
    """A tool scoped to one device must not sit in the shared toolbox, or the
    next porter will try to run it and wonder why it makes no sense."""
    bad = []
    for path in tools():
        scope = field(path, "scope") or ""
        in_profile = "profiles" in path.parts
        if scope.startswith("device:") and not in_profile:
            bad.append(f"{path.name}: {scope} but lives in tools/")
    assert not bad, "\n  ".join(bad)


def test_no_personal_paths_or_hosts():
    """The whole point of the config layer. A home directory or a user@ip in a
    tool means it works on exactly one desk."""
    bad = []
    for path in tools():
        for m in PERSONAL.finditer(path.read_text(errors="replace")):
            bad.append(f"{path.name}: {m.group(0)!r}")
    assert not bad, ("personal paths/hosts must come from config:\n  "
                     + "\n  ".join(bad))


def test_no_hardcoded_gadget_ip_outside_config():
    """The gadget IP is a legitimate default -- in lib/ and in profiles, where
    it is data. In a tool it is a hardcoded assumption."""
    bad = []
    for path in tools():
        text = path.read_text(errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if GADGET_IP in line and "PORTHOLE_" not in line and "$HOST" not in line:
                bad.append(f"{path.name}:{i}: {line.strip()[:70]}")
    assert not bad, ("use $HOST or $PORTHOLE_HOST instead:\n  "
                     + "\n  ".join(bad))


def test_shell_tools_parse():
    bad = []
    for path in tools():
        if path.suffix != ".sh" and not _is_shell(path):
            continue
        proc = subprocess.run(["bash", "-n", str(path)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            bad.append(f"{path.name}: {proc.stderr.strip().splitlines()[:1]}")
    assert not bad, "shell syntax errors:\n  " + "\n  ".join(bad)


def test_python_tools_compile():
    bad = []
    for path in tools():
        if path.suffix != ".py":
            continue
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            bad.append(f"{path.name}: {proc.stderr.strip().splitlines()[-1:]}")
    assert not bad, "python syntax errors:\n  " + "\n  ".join(bad)


def test_executable_tools_have_a_shebang():
    bad = []
    for path in tools():
        if not os.access(path, os.X_OK):
            continue
        if not path.read_bytes()[:2] == b"#!":
            bad.append(path.name)
    assert not bad, "executable but no shebang:\n  " + "\n  ".join(bad)


def test_tk_lib_is_a_symlink_to_the_shared_lib():
    """It has been materialised into a real file by a stray `sed -i` before.
    That silently forks the shared library: edits to lib/porthole.sh stop
    reaching every shell tool, and nothing errors."""
    link = ROOT / "tools" / "tk-lib.sh"
    assert link.is_symlink(), (
        f"{link} must be a symlink to ../lib/porthole.sh, not a copy "
        f"(a `sed -i` over tools/*.sh will do this -- use `sed --follow-symlinks` "
        f"or exclude it)")
    assert link.resolve() == (ROOT / "lib" / "porthole.sh").resolve()


def test_tools_that_need_a_device_mention_the_mutex_or_use_the_lib():
    """Any tool touching the shared device must either take the mutex itself or
    source the lib that provides it. A tool that opens its own ssh with neither
    is how two agents end up driving one phone."""
    bad = []
    for path in tools():
        needs = (field(path, "needs") or "").upper()
        if not needs.startswith(("BOOTED", "FASTBOOT", "ANY")):
            continue
        text = path.read_text(errors="replace")
        if field(path, "lib-exempt"):
            continue      # declared and justified in the tool's own header
        ok = ("tk-lib.sh" in text or "import porthole" in text
              or "tk-device.sh" in text or "porthole.Device" in text
              or "TK_SSH_OPTS" in text or "tk_device_state" in text)
        if not ok:
            bad.append(f"{path.name} (needs {needs})")
    assert not bad, ("device tools must use the shared lib or the mutex, or\n"
                     "declare `# lib-exempt: <why>` in their header:\n  "
                     + "\n  ".join(bad))


def _is_shell(path):
    return path.read_bytes()[:2] == b"#!" and b"sh" in path.read_bytes()[:40]


# ------------------------------------------------------------------- runner --

def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}:\n  {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed  ({len(tools())} tools checked)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
