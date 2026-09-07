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
from concurrent.futures import ThreadPoolExecutor
import tempfile
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

REQUIRED_FIELDS = ("scope", "needs", "env", "exits")
VALID_SCOPE = re.compile(r"^(generic|soc:[a-z0-9_-]+|device:[a-z0-9-]+)$")
VALID_NEEDS = re.compile(
    r"^(-(\s|$)|BOOTED\b|FASTBOOT\b|FROZEN\b|INITRAMFS\b|any\b|on-device\b)")

# Personal paths and hosts moved to lib/porthole_secrets.py, scanned over
# everything git tracks by tests/test_secrets.py. Scanning tools() was the
# defect: the serial that prompted it landed in tests/, profiles/, brain/ and a
# commit message, none of which this file has ever looked at. Do not re-add a
# copy here -- duplication with drift is worse than either copy alone.

# The pmOS USB-gadget address is a real default, not a personal one. It is
# legitimate in lib/ and in a profile; in a tool it should come from config.
GADGET_IP = "172.16.42.1"


def tools():
    """One definition of "a tool", shared with the CLI so the contract tests and
    `porthole tools` can never disagree about what they are checking."""
    from porthole_cmd_tools import collect
    return [t.path for t in collect(ROOT)] + [
        t.path for profile in sorted((ROOT / "profiles").iterdir())
        if profile.is_dir() and not profile.name.startswith("_")
        for t in collect(ROOT, profile.name)
        if "profiles" in t.path.parts]


def head_of(path, lines=30):
    return "".join(path.read_text(errors="replace").splitlines(True)[:lines])


def field(path, name):
    m = re.search(rf"^#\s*{name}:\s*(.*)$", head_of(path), re.M)
    return m.group(1).strip() if m else None


# ------------------------------------------------------------- the contract --

def _concurrently(check, paths):
    """Run `check(path)` over every path at once, collecting the complaints.

    Three tests here spawn one subprocess per tool across 117 tools, which was
    17.9s of a 73s suite -- all of it waiting. Threads rather than processes
    because the work IS a subprocess call: the GIL is released for its whole
    duration, and nothing has to be pickled.
    """
    workers = min(len(paths), (os.cpu_count() or 1) * 4) or 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [bad for bad in pool.map(check, paths) if bad]


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
                     "INITRAMFS | "
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
    def check(path):
        if path.suffix != ".sh" and not _is_shell(path):
            return None
        proc = subprocess.run(["bash", "-n", str(path)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            return f"{path.name}: {proc.stderr.strip().splitlines()[:1]}"
        return None

    bad = _concurrently(check, tools())
    assert not bad, "shell syntax errors:\n  " + "\n  ".join(bad)


def test_python_tools_compile():
    def check(path):
        if path.suffix != ".py":
            return None
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            return f"{path.name}: {proc.stderr.strip().splitlines()[-1:]}"
        return None

    bad = _concurrently(check, tools())
    assert not bad, "python syntax errors:\n  " + "\n  ".join(bad)


def test_host_python_tools_can_start():
    """Compiling is not running.

    tools/tsh.py shipped with `os.environ.get(...)` as an argparse default and
    no `import os`. py_compile passed it -- the NameError is at runtime -- and
    the tool was dead. It is the ONLY channel to the initramfs debug shell, so
    it was broken precisely for the case it exists for, and nothing noticed
    until a device would not boot on 2026-08-27.

    `--help` is the cheapest thing that actually executes module scope and
    builds the parser, which is where that class of bug lives. Restricted to
    host-only tools: anything with `needs:` naming a device state or on-device
    may legitimately touch hardware just by importing.
    """
    root = tempfile.mkdtemp(prefix="porthole-smoke-")

    def check(path):
        if path.suffix != ".py":
            return None
        # Everything except on-device tools: a tool declaring BOOTED or
        # INITRAMFS still RUNS on the host, and is exactly as safe to --help.
        # Keying on "host only" instead would have excluded tsh.py, the tool
        # this test was written for, the moment its needs: was made accurate.
        if (field(path, "needs") or "").strip().startswith("on-device"):
            return None
        # A cwd PER TOOL, not one shared between them. In a throwaway cwd
        # because a tool that takes an output path as argv[1] treats "--help"
        # as one: tk-tone.py wrote a WAV named `--help` into the repo root the
        # first time this ran. Now that these run concurrently, a shared cwd
        # would let two such tools race for the same filename.
        scratch = os.path.join(root, path.stem)
        os.makedirs(scratch, exist_ok=True)
        # stdin=DEVNULL, or a tool that prompts (tk-mount-cal.py walks you
        # through four physical poses) blocks forever on input() instead of
        # failing. The timeout stays as a backstop, not as the mechanism.
        proc = subprocess.run([sys.executable, str(path), "--help"],
                              capture_output=True, text=True, cwd=scratch,
                              stdin=subprocess.DEVNULL, timeout=30)
        # Only the "this line has never been executed" class counts. A tool
        # without argparse may well die on `--help` with a ValueError from
        # int(sys.argv[1]) -- that is the tool working. NameError and
        # ImportError are different: they mean the code cannot run at all, for
        # any argument, and no amount of testing on the device would have been
        # reached to find out.
        for fatal in ("NameError", "ImportError", "ModuleNotFoundError"):
            if f"{fatal}:" in proc.stderr:
                return f"{path.name}: {proc.stderr.strip().splitlines()[-1:]}"
        return None

    bad = _concurrently(check, tools())
    assert not bad, "python tools that cannot run at all:\n  " + "\n  ".join(bad)


def test_executable_tools_have_a_shebang():
    bad = []
    for path in tools():
        if not os.access(path, os.X_OK):
            continue
        if not path.read_bytes()[:2] == b"#!":
            bad.append(path.name)
    assert not bad, "executable but no shebang:\n  " + "\n  ".join(bad)


def test_tools_with_a_shebang_are_executable():
    """The converse of the test above, and the one that was missing.

    Thirty-two tools -- every Easel and FTM4 probe among them -- were committed
    mode 644 with a shebang. `porthole run` reached posix_spawn and returned a
    raw PermissionError traceback, which reads as "porthole crashed" rather than
    "chmod +x this file". Profile tools are the usual victims: they are added by
    hand and nothing on the way in checked the mode.

    A file with a shebang is asking to be executed. `tools/ph-build.sh` is
    deliberately not one -- it must be SOURCED -- and carries no shebang, which
    is what keeps it out of this check.
    """
    bad = []
    for path in tools():
        if path.is_symlink() or path.read_bytes()[:2] != b"#!":
            continue
        if not os.access(path, os.X_OK):
            bad.append(str(path.relative_to(ROOT)))
    assert not bad, ("has a shebang but is not executable:\n  "
                     + "\n  ".join(sorted(bad))
                     + "\n\nchmod +x them, and commit the mode change.")


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


BASHISMS = (
    ("BASH_SOURCE", "BASH_SOURCE is undefined in POSIX sh -- ash reports "
                    "'bad substitution' and the path resolves empty"),
    ("[[", "[[ ]] is a bash keyword"),
    ("declare ", "declare is a bash builtin"),
    ("local -n", "namerefs are bash-only"),
)


def test_no_bashisms_in_posix_sh_scripts():
    """pmOS ships busybox ash as /bin/sh. A bashism in a `#!/bin/sh` script does
    not degrade -- it is a syntax error at the point of use.

    This exists because a bulk edit added `${BASH_SOURCE[0]:-$0}` to eight
    `#!/bin/sh` tools. Every one of them silently failed to find tk-lib.sh, and
    only shellcheck in CI noticed."""
    bad = []
    for path in tools():
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if not text.startswith("#!") or "bash" in text.splitlines()[0]:
            continue
        if "sh" not in text.splitlines()[0]:
            continue
        for token, why in BASHISMS:
            for i, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if token in line:
                    bad.append(f"{path.name}:{i}: {token} -- {why}")
    assert not bad, ("bashisms in POSIX sh scripts:\n  " + "\n  ".join(bad))


def test_the_python_floor_is_declared_consistently():
    """bin/porthole, the Makefile and the CI matrix must agree on the oldest
    interpreter supported. When they drift, local checks pass against a newer
    python and CI rejects syntax the developer cannot see -- which is exactly
    how a PEP 701 f-string reached main."""
    launcher = (ROOT / "bin" / "porthole").read_text()
    m = re.search(r"sys\.version_info\s*<\s*\((\d+),\s*(\d+)\)", launcher)
    assert m, "bin/porthole must declare a version floor"
    floor = f"{m.group(1)}.{m.group(2)}"

    makefile = (ROOT / "Makefile").read_text()
    m = re.search(r"^PY_FLOOR\s*:?=\s*(\S+)", makefile, re.M)
    assert m, "the Makefile must declare PY_FLOOR"
    assert m.group(1) == floor, (
        f"Makefile PY_FLOOR={m.group(1)} but bin/porthole requires {floor}")

    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    m = re.search(r'python:\s*\[([^\]]+)\]', ci)
    assert m, "the CI matrix must list python versions"
    versions = [v.strip().strip('"\'') for v in m.group(1).split(",")]
    assert floor in versions, (
        f"CI matrix {versions} does not test the declared floor {floor}")


def _ci_run_commands(text):
    """Every shell line a `run:` step in ci.yml executes, block scalars included.

    Hand-rolled rather than pyyaml: the tests run on a bare runner with nothing
    installed, which is the whole point of them."""
    lines, out = text.splitlines(), []
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)run:\s*(\S.*)?$", line)
        if not m:
            continue
        indent, inline = m.group(1), (m.group(2) or "").strip()
        if inline and inline not in ("|", ">", "|-", ">-"):
            out.append(inline)
            continue
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt.startswith(indent + " "):
                break
            if nxt.strip() and not nxt.strip().startswith("#"):
                out.append(nxt.strip())
    return out


def _make_prerequisites(text):
    """target -> its prerequisites, close enough for a Makefile with no
    pattern rules. Recipe lines start with a tab, so they cannot match."""
    return {m.group(1): m.group(2).split()
            for m in re.finditer(r"^([a-z][a-z0-9-]*):([^=#\n]*)", text, re.M)}


# Only a dependency install may live in the workflow. Everything else is a step,
# and steps belong to the Makefile.
CI_SETUP = re.compile(r"^(sudo )?(apt-get|python3? -m pip|pip3?) ")


def test_ci_runs_nothing_but_make_targets_that_make_ci_also_runs():
    """The one rule that stops "it passed locally" from drifting from CI.

    The workflow, the Makefile and tests/ci-local.sh each held their own copy of
    the step list, and each copy was missing something the others had -- brain
    lint locally, the console job locally, the doctor assertions in the
    simulation. So a contributor ran `make check`, saw green, pushed, and CI
    went red on a step their laptop had never run.

    Two assertions close it: ci.yml may run nothing but `make <target>` (bar
    installing a dependency), and every target it names must be reachable from
    `make ci`. Adding a CI step now means adding it to a make target, which is
    the same thing as adding it to what a developer runs."""
    cmds = _ci_run_commands((ROOT / ".github/workflows/ci.yml").read_text())
    assert cmds, "no run: steps found -- suspect this parser, not the workflow"

    stray = [c for c in cmds if not c.startswith("make ") and not CI_SETUP.match(c)]
    assert not stray, (
        "CI must run make targets, not its own copy of the steps -- move these\n"
        "into a target so `make ci` runs them too:\n  " + "\n  ".join(stray))

    named = {c.split()[1] for c in cmds if c.startswith("make ")}
    prereqs = _make_prerequisites((ROOT / "Makefile").read_text())
    reachable, queue = set(), ["ci"]
    while queue:
        t = queue.pop()
        if t not in reachable:
            reachable.add(t)
            queue += prereqs.get(t, [])
    missing = sorted(named - reachable)
    assert not missing, (
        f"CI runs {missing}, but `make ci` does not reach {'it' if len(missing) == 1 else 'them'}."
        f"\nAdd to the prerequisites of the ci: target in the Makefile.")


def _is_shell(path):
    return path.read_bytes()[:2] == b"#!" and b"sh" in path.read_bytes()[:40]


# ------------------------------------------------------------------- runner --

def main():
    # The count is what makes a green run meaningful: "18/18 passed" says
    # nothing about whether the walk found any tools at all. Folded onto the
    # runner's summary line, not printed separately -- `make floor` and
    # `make smoke` only look at a suite's LAST line.
    return _runner.run(globals(), "({} tools checked)".format(len(tools())))


def test_the_build_path_never_invokes_ssh_without_the_shared_options():
    """TK_SSH_OPTS is where -i "$PORTHOLE_SSH_KEY" -o IdentitiesOnly=yes lives
    (lib/porthole.sh:188), along with ConnectTimeout, BatchMode and the
    connection mux.

    tkmod's two scps and its two sshs omitted it, so in the workspace -- where
    PORTHOLE_SSH_KEY is /run/porthole/device_key, the ONLY key the container
    has -- the device key was never offered. Every `porthole build mod --yes`
    ended `scp: Connection closed`, which names nothing and reads like a
    network fault; a session went looking at the ssh control master instead.

    Scoped to the files every build and push routes through, and asserted here
    rather than left to review: patching the one line a report names leaves
    every sibling caller just as broken, and there were four.

    tools/ that hand-roll their own option sets (stallwatch, tk-recover,
    tk-stream) are deliberately outside this: they are host-side freeze
    detectors whose whole job is for ssh to time out, and the mux would mask
    exactly what they watch for. They do not push anything to a device.
    """
    bare = re.compile(r'(?<![\w./-])(ssh|scp)\s')
    offenders = []
    for name in ("tools/ph-build.sh", "lib/porthole.sh"):
        path = ROOT / name
        for n, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "TK_SSH_OPTS" in line:
                continue
            # Prose and here-doc text mention ssh constantly; only a line that
            # STARTS a command is an invocation.
            if not re.match(r'^(ssh|scp)\s|[;&|(]\s*(ssh|scp)\s|'
                            r'^\s*(timeout\s+\S+\s+)?(ssh|scp)\s', stripped):
                continue
            if bare.search(stripped):
                offenders.append(f"{name}:{n}: {stripped[:70]}")
    assert not offenders, (
        "these invoke ssh/scp without TK_SSH_OPTS, so they offer no device "
        "key:\n  " + "\n  ".join(offenders))

# A `&&` chain whose head is a TEST: `[ -s f ] && echo`. `grep -q x && warn` is
# the same shape, and so is `command -v foo && use-it`.
_GUARD_HEAD = re.compile(r"^(\[\[?\s|test\s|grep\s|command\s+-v\s|pgrep\s)")
# A tail that is itself a test makes the function a PREDICATE, which is correct
# code -- _ph_defconfig_current is four tests joined by `&&` and its status is
# the answer. `return`/`exit` say the status out loud. Everything else is a
# side effect standing where the return value should be.
_DELIBERATE_TAIL = re.compile(r"^(return|exit|:)\b")


def _last_statement(lines, close):
    """The last statement of the function that ends at `lines[close]`.

    Walks back over blanks and comments to the final line, then keeps going
    while the line before it ends in a continuation -- `&&`, `||`, `|` or a
    backslash. Without that, the guard and its body look like two statements
    and the one that matters is invisible: the bug this test was written for
    is written across two lines, with the `&&` at the end of the first."""
    j = close - 1
    while j >= 0 and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
        j -= 1
    if j < 0:
        return ""
    start = j
    while start > 0 and re.search(r"(&&|\|\||\||\\)\s*$", lines[start - 1]):
        start -= 1
    return " ".join(line.strip() for line in lines[start:j + 1])


def test_no_shell_function_ends_in_a_test_that_guards_a_side_effect():
    """Issue #58: a function's exit status is its last command's.

    `tkpush-modules` ended with

        [ -s "$_PH_REPO/.device-uuids" ] &&
            echo ">> recorded device UUIDs: ..."

    and on taimen -- which boots by partition, so its cmdline carries no
    `pmos_*_uuid=` -- that file is empty. The test is false, the echo never
    runs, and the FUNCTION returns 1. The `fast` rung then aborted after
    pushing 271 modules and before flashing, twice on 2026-09-04, over a line
    whose absence the flash step already handles with a warning.

    Nothing about the push failed. The last line was a report.

    Not shellcheck's job: SC2015 is about `a && b || c` precedence, and says
    nothing about where in a function the construct sits. This is the position
    that makes it a bug.

    Write the intent instead -- an `if`, or an explicit `return 0`."""
    bad = []
    for path in tools() + [ROOT / "lib" / "porthole.sh"]:
        if path.suffix != ".sh":
            continue
        lines = path.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines):
            if line.rstrip() != "}":          # a function close, at column 0
                continue
            statement = _last_statement(lines, i)
            if "&&" not in statement or "||" in statement:
                continue
            if not _GUARD_HEAD.match(statement):
                continue
            tail = statement.rsplit("&&", 1)[1].strip()
            if _GUARD_HEAD.match(tail) or _DELIBERATE_TAIL.match(tail):
                continue
            bad.append(f"{path.name}:{i + 1}: {statement[:90]}")
    assert not bad, (
        "a function whose last statement is a guarded side effect returns the "
        "GUARD, so a false test fails the function:\n  " + "\n  ".join(bad))


def test_a_missing_objcopy_is_cannot_compare_not_a_crc_mismatch():
    """Issue #57: exit codes are an API, and 1 is an ANSWER.

    tk-modcrc.py gates `porthole build mod`: 1 means "these two modules
    disagree" and the rung refuses the push on it. Inside the workspace there
    is no llvm-objcopy on PATH, `subprocess.run` raised FileNotFoundError, and
    a traceback exits 1 -- so every venus_core push for a week was refused
    with a message about CONFIG skew, against a module whose CRCs matched.

    69 is the code for "cannot compare", and the rung treats it as not-a-
    refusal (tests/test_ph_build.sh covers that half). This is the half that
    has to produce it. PATH is emptied rather than mocked because the tool
    finding a stray objcopy is the whole failure mode.

    stderr is the positive control: it proves the run reached the `for/else`
    over the objcopy candidates, rather than exiting 69 somewhere earlier."""
    with tempfile.TemporaryDirectory() as d:
        ko = pathlib.Path(d, "empty.ko")
        ko.write_bytes(b"")
        nowhere = pathlib.Path(d, "bin")
        nowhere.mkdir()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "tk-modcrc.py"),
             str(ko), str(ko)],
            capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PATH=str(nowhere)))
    assert proc.returncode == 69, (
        f"no objcopy on PATH exited {proc.returncode}; 1 would tell the mod "
        f"rung the CRCs disagree\n{proc.stderr.strip()}")
    assert "could not extract __versions" in proc.stderr, (
        "it exited 69 without reaching the objcopy candidates -- this test is "
        f"no longer measuring what it says\n{proc.stderr.strip()}")


if __name__ == "__main__":
    sys.exit(main())
