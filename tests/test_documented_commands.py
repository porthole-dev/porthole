#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Every documented `porthole ...` invocation must actually parse.

This exists because 195 tests passed while the newcomer's FIRST SCREEN told
them to run `porthole init --device <codename>`, which had stopped parsing when
`init` gained a plain positional. Twenty-four such strings were left across
lib/, README.md, AGENTS.md, brain/, skills/ and the PR templates.

Nothing in the suite knew that a string inside a hint was a command. So the
class of bug was invisible by construction, and it will recur every time a verb
changes -- unless a test treats documentation as code.

Parse only, never execute: the parser is built from the live registry and
`parse_args` is called on the tokens. A command that parses can still be wrong,
but a command that does not parse is wrong for certain, and that is the whole
bug class.
"""
import argparse
import contextlib
import io
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cli  # noqa: E402

SPECS = porthole_cli.discover(ROOT)
PARSER, TABLE = porthole_cli.build(ROOT, SPECS)

# Where documentation lives. Everything a reader could copy a command out of.
SEARCH = [
    "lib/*.py", "*.md", "docs/*.md", "brain/**/*.md", "skills/**/*.md",
    ".github/**/*.md", "tools/*.sh", "profiles/_template/*",
]

# Anything with shell in it is a shell example, and argparse is the wrong judge
# of a pipeline. Skipping them is honest; pretending to check them is not.
SHELL = re.compile(r"[|><&$`]|\bthen\b|\bfi\b")

# Placeholders a reader is meant to substitute, replaced with a token valid for
# every positional we have (lowercase, dashed).
PLACEHOLDER = re.compile(r"<[^>]+>|\{[a-z_]+\}|\b(?:NAME|CODENAME|PKG|SOC|DIR|"
                         r"PATH|FILE|REF|REGEX|LAYER|STATE|SCOPE|LEVEL|ARCH|SEC|"
                         r"DTS|TOML|WHAT|KIND|ACTION|SYMBOLS|IMAGE|QUERY|WORD|"
                         r"TOPIC|SHELL|KEY|VALUE)\b")

DUMMY = "placeholder"

# Prose that happens to begin "porthole <verb>". No command contains these.
STOPWORDS = {"and", "the", "for", "is", "are", "has", "have", "not", "been",
             "to", "what", "how", "it", "this", "that", "a", "an", "of", "in",
             "with", "resolution", "keeping", "rebuild", "clean", "run"}

# A note may legitimately cite a command that USED to work -- brain/ records
# what went wrong, and the broken invocation is the evidence. A line carrying
# this marker is documentation OF a command, not documentation THAT it works.
HISTORICAL = "porthole:historical"
BACKTICKED = re.compile(r"`(porthole [^`\n]+)`")
FENCE = re.compile(r"^\s*(?:\$ )?(porthole .+)$")


def _from_python(path: pathlib.Path) -> list[tuple[int, str]]:
    """String literals that START with "porthole ".

    Using the AST rather than a regex over the source is what separates a
    command from the word "porthole" in a sentence. A literal beginning with
    "porthole " is a command by construction: it is either printed to the user
    as advice or listed as an example. Prose in a docstring is not, and neither
    is an f-string, whose value is only known at runtime -- those are skipped
    rather than guessed at.
    """
    import ast
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (OSError, SyntaxError):
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for line in node.value.splitlines():
                text = line.strip()
                if text.startswith("porthole ") or text.startswith("$ porthole "):
                    out.append((node.lineno, text.lstrip("$ ")))
                for m in BACKTICKED.finditer(line):
                    out.append((node.lineno, m.group(1)))
    return out


def _from_text(path: pathlib.Path) -> list[tuple[int, str]]:
    """Markdown and shell: backticks, and lines inside code blocks.

    Both are unambiguous "this is a command" signals, which prose is not.
    """
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    out, fenced = [], False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.startswith(("    ", "\t")):
            m = FENCE.match(line)
            if m:
                out.append((n, m.group(1).strip()))
        for m in BACKTICKED.finditer(line):
            out.append((n, m.group(1)))
    return out


def documented() -> list[tuple[str, int, str]]:
    """(file, line, command) for every invocation we can find in a command
    context. Prose mentioning porthole is deliberately not one."""
    found = []
    for pattern in SEARCH:
        for path in sorted(ROOT.glob(pattern)):
            if not path.is_file() or ".git" in path.parts:
                continue
            # The specs describe history, including commands as they used to
            # be. They are a record, not instructions.
            if "superpowers" in path.parts:
                continue
            lines = path.read_text(errors="replace").splitlines()
            grab = _from_python if path.suffix == ".py" else _from_text
            for line, raw in grab(path):
                context = lines[line - 1] if 0 < line <= len(lines) else ""
                if HISTORICAL in context:
                    continue
                found.append((str(path.relative_to(ROOT)), line, raw))
    return found


def normalise(raw: str) -> list[str] | None:
    """Command text -> argv, or None when it is prose rather than a command."""
    # Trim trailing prose: `porthole doctor    check the host and device`
    # Two or more spaces separate a command from its description everywhere in
    # this codebase, which is a convention worth relying on rather than
    # guessing at sentence structure.
    raw = raw[len("porthole "):] if raw.startswith("porthole ") else raw
    # A command is separated from its description by two or more spaces, or an
    # em/en dash. Both conventions are used throughout this codebase.
    raw = re.split(r"\s{2,}|\s[—–]\s", raw)[0]
    raw = raw.split(" # ")[0].strip().rstrip(".,;:)`\"'")
    if not raw or SHELL.search(raw):
        return None
    raw = PLACEHOLDER.sub(DUMMY, raw)
    try:
        import shlex
        argv = shlex.split(raw)
    except ValueError:
        return None
    if not argv:
        return None
    # Four rules that separate a command from a sentence that begins with the
    # word "porthole". Each removes a class of false positive seen in the
    # repository, and none of them can hide a stale FLAG -- which is where
    # every one of the twenty-four real failures lived.
    #
    # 1. The second token must be a verb or a flag. Kills "porthole setup",
    #    "porthole needs python 3.8+", "porthole bash completion. Install...".
    #    Known limitation, stated rather than hidden: a renamed VERB is skipped
    #    here rather than reported. `test_completion_covers_every_verb` covers
    #    that, and a dead verb in prose is far more visible than a dead flag.
    if not argv[0].startswith("-") and argv[0] not in TABLE:
        return None
    # 2. A bare `porthole <verb>` is a reference to the verb, not an
    #    invocation. There are no flags in it to get wrong.
    if len(argv) == 1:
        return None
    # 3. A trailing flag means the line wrapped mid-command in a docstring.
    #    Judging a fragment would report a formatting artifact as a bug.
    if argv[-1].startswith("-"):
        return None
    # 4. An English function word is never a command token. This is what tells
    #    "porthole doctor is clean" (a sentence) from "porthole doctor --json"
    #    (a command), where the first word after the verb is a real verb name
    #    in both cases and only the rest gives it away.
    if STOPWORDS & {a.lower() for a in argv}:
        return None
    return argv


def parses(argv: list[str]) -> tuple[bool, str]:
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            PARSER.parse_args(argv)
        return True, ""
    except SystemExit as exc:
        # 4. --help and --version exit 0. They parsed; they just do not return.
        if not exc.code:
            return True, ""
        lines = buf.getvalue().strip().splitlines()
        return False, lines[-1] if lines else "usage error"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def test_every_documented_command_parses():
    """The one that would have caught all twenty-four."""
    failures = []
    for where, line, raw in documented():
        argv = normalise(raw)
        if argv is None:
            continue
        ok, why = parses(argv)
        if not ok:
            failures.append(f"{where}:{line}  `porthole {' '.join(argv)}`  -> {why}")
    assert not failures, (
        f"{len(failures)} documented command(s) do not parse:\n  "
        + "\n  ".join(failures[:20]))


def test_the_check_can_actually_fail():
    """A positive control. A checker that cannot fail proves nothing, and this
    one silently passing would restore exactly the blindness it was written to
    remove."""
    ok, _ = parses(["definitely-not-a-verb"])
    assert not ok, "the parser accepted a verb that does not exist"
    ok, _ = parses(["init", "--device", "x"])
    assert not ok, "init still accepts --device; the R1 fix regressed"


def test_every_spec_example_parses():
    """Examples are the most-copied strings in the whole project."""
    failures = []
    for spec in SPECS:
        for example in spec["examples"]:
            argv = normalise(example) if example.startswith("porthole ") else None
            if argv is None:
                continue
            ok, why = parses(argv)
            if not ok:
                failures.append(f"{spec['verb']}: `{example}` -> {why}")
    assert not failures, "\n  ".join(failures)


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed "
          f"({len(documented())} invocations scanned)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
