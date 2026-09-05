# SPDX-License-Identifier: MIT
"""`porthole tools` -- search and describe the toolbox.

Ninety-odd tools is more than anyone can hold in their head, and "list the
whole directory before concluding a tool does not exist" is advice that has
already failed once in practice. This verb makes the toolbox navigable: filter
by what it touches, what device state it needs, or what it is called, and read
its contract without opening it.

It is equally the machine-readable catalogue an agent should consult first --
`porthole tools --json` beats `ls tools/` plus 90 guesses.
"""
from __future__ import annotations

import os
import pathlib
import re

from porthole_cli import Bail, EX_FAIL, EX_OK

FIELD_RE = re.compile(
    r"^#\s*(scope|needs|env|exits|gives|device|lib-exempt):\s*(.*)$", re.M)
# The first comment line after the shebang that is not a field: the summary.
SUMMARY_RE = re.compile(r"^#\s*(\S.*?)\s*$", re.M)

REQUIRED_FIELDS = ("scope", "needs", "env", "exits")

# A licence header is not a description. 49 of 114 tools were reporting
# "SPDX-License-Identifier: MIT" as their summary, and `tools lint` called them
# all self-describing -- the check tested that a summary was non-empty, which a
# licence line satisfies while telling a reader nothing at all.
NOT_A_SUMMARY = re.compile(
    r"^(SPDX-License-Identifier|Copyright|SPDX-FileCopyrightText)\b", re.I)


class Tool:
    def __init__(self, path: pathlib.Path, root: pathlib.Path):
        self.path = path
        self.root = root
        self.head = ""
        self.fields: dict[str, str] = {}
        self.summary = ""
        self._parse()

    def _parse(self) -> None:
        try:
            lines = self.path.read_text(errors="replace").splitlines(True)
        except OSError:
            return
        self.head = "".join(lines[:30])
        for key, value in FIELD_RE.findall(self.head):
            self.fields[key] = value.strip()
        self.summary = self._summary(lines)

    def _summary(self, lines) -> str:
        """First line of the docstring for python, first real comment for shell.

        Python tools carry their description in a module docstring, not a
        comment block, so a comment-only scan returns nothing for half the
        toolbox -- which is how the generated catalogue ended up with empty
        cells.
        """
        text = "".join(lines)
        if self.path.suffix == ".py":
            m = re.search(r'^\s*(?:[ru]?["\']{3})(.+?)$', text, re.M)
            if m and m.group(1).strip():
                return m.group(1).strip()
        # Shell: the first comment line that is not the shebang, not a field,
        # not a field's wrapped continuation, and not a divider.
        skipping_field = False
        for line in lines[:20]:
            stripped = line.strip()
            if stripped.startswith("#!"):
                continue
            if NOT_A_SUMMARY.match(stripped.lstrip("# ").strip()):
                continue
            if not stripped.startswith("#"):
                if stripped:
                    break
                continue
            if FIELD_RE.match(stripped):
                skipping_field = True
                continue
            body = stripped.lstrip("#").strip()
            if not body or set(body) <= set("-=# "):
                skipping_field = False
                continue
            if skipping_field and line.lstrip("#").startswith("   "):
                # An indented wrap of the field above, not the summary.
                continue
            skipping_field = False
            return body
        return ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def scope(self) -> str:
        return self.fields.get("scope", "")

    @property
    def needs(self) -> str:
        return self.fields.get("needs", "")

    @property
    def kind(self) -> str:
        return "profile" if "profiles" in self.path.parts else "core"

    @property
    def gaps(self) -> list[str]:
        missing = [f for f in REQUIRED_FIELDS if f not in self.fields]
        # "Has a summary" is not the same as "says what it does". The lint
        # passed 114 tools while 49 of them described themselves as
        # "SPDX-License-Identifier: MIT" -- a check that only tested for
        # non-empty could not tell a description from a licence.
        if not self.summary.strip() or NOT_A_SUMMARY.match(self.summary):
            missing.append("summary")
        return missing

    def as_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind,
                "path": str(self.path.relative_to(self.root)),
                "summary": self.summary,
                "scope": self.scope, "needs": self.needs,
                "env": self.fields.get("env", ""),
                "exits": self.fields.get("exits", ""),
                "gives": self.fields.get("gives", ""),
                "language": "python" if self.path.suffix == ".py" else "shell",
                "executable": os.access(self.path, os.X_OK),
                "documented": not self.gaps,
                "missing_fields": self.gaps}

    def read(self) -> str:
        """The whole file. `head` is the first 30 lines, which is the right
        amount for a catalogue and the wrong amount for a contract check --
        `pkill` and `sleep` live in the body."""
        try:
            return self.path.read_text(errors="replace")
        except OSError:
            return ""


# Files that live in tools/ but are not tools: systemd units are installed on
# the device, and listing them in the catalogue invites someone to run them.
NOT_TOOLS = (".service", ".timer", ".rules", ".conf", ".md", ".txt")


def collect(root: pathlib.Path, device: str = "") -> list[Tool]:
    def usable(p):
        return (p.is_file() and not p.is_symlink()
                and not p.name.startswith(".")
                and p.suffix not in NOT_TOOLS
                and p.name != "__pycache__")

    paths = [p for p in sorted((root / "tools").iterdir()) if usable(p)]
    if device:
        pdir = root / "profiles" / device / "tools"
        if pdir.is_dir():
            paths += [p for p in sorted(pdir.iterdir()) if usable(p)]
    return [Tool(p, root) for p in paths]


def cmd_tools(args, ctx) -> int:
    try:
        device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    except Exception:  # noqa: BLE001 -- listing must survive a broken profile
        device = ""
    tools = collect(ctx.root, device)

    # A leading ACTION word is a mode; anything else is a tool name. The
    # collision set is three words now, and one of them is no longer free of
    # collateral: tk-daily-audit.sh contains "audit", so
    # `porthole tools audit` no longer substring-matches it -- it stays
    # reachable by its full name or via `--grep audit`. That trade is
    # accepted.
    action = ""
    if args.name in ("list", "lint", "audit"):
        action, args.name = args.name, None

    if action == "lint":
        bad = [t for t in tools if t.gaps]
        payload = [{"name": t.name, "missing": t.gaps} for t in bad]

        def render_lint():
            if not bad:
                ctx.out(ctx.out.paint(
                    f"all {len(tools)} tools are self-describing", "green"))
                return
            width = max(len(t.name) for t in bad)
            for tool in bad:
                ctx.out(f"  {tool.name:<{width}}  missing: "
                        f"{ctx.out.paint(', '.join(tool.gaps), 'yellow')}")
            ctx.out.blank()
            ctx.out(f"{len(bad)} of {len(tools)} tools incomplete. "
                    f"Header format: docs/CONTRIBUTING.md")
        ctx.emit(payload, render_lint)
        return EX_FAIL if bad else EX_OK

    if action == "audit":
        from porthole_toolcontract import audit as run_audit
        rows = run_audit(tools)

        def render_audit():
            if not rows:
                ctx.out(ctx.out.paint(
                    f"all {len(tools)} tools hold to the contract", "green"))
                return
            locs = [(row, finding, f"{row['name']}:{finding['line']}")
                    for row in rows
                    # errors before warnings within a row, so the two
                    # severities do not interleave in CHECKS order
                    for finding in sorted(
                        row["findings"],
                        key=lambda f: f["severity"] != "error")]
            width = max(len(loc) for _, _, loc in locs)
            for row, finding, loc in locs:
                colour = "red" if finding["severity"] == "error" else "yellow"
                ctx.out(f"  {loc:<{width}}  "
                        f"{ctx.out.paint(finding['check'], colour)}: "
                        f"{finding['detail']}")
            ctx.out.blank()
            errors = sum(r["errors"] for r in rows)
            warnings = sum(r["warnings"] for r in rows)
            ctx.out(f"{len(rows)} of {len(tools)} tools have findings: "
                    f"{errors} error, {warnings} warning. "
                    f"Contract: docs/DESIGN-unattended-autonomy.md")
        ctx.emit(rows, render_audit)
        # A finding is not a broken toolbox, and phase 1 has not run yet.
        # Exiting non-zero here would make `make check` red for everyone
        # before there is anything they can do about it.
        return EX_OK

    if args.name:
        # Exact match, then substring: `porthole tools suspend` should work.
        exact = [t for t in tools if t.name == args.name
                 or t.path.stem == args.name]
        chosen = exact or [t for t in tools if args.name in t.name]
        if not chosen:
            raise Bail(f"no tool matching {args.name!r}", EX_FAIL,
                       "`porthole tools` lists them all")
        if len(chosen) == 1:
            return show(chosen[0], ctx)
        tools = chosen
    else:
        if args.scope:
            tools = [t for t in tools if t.scope == args.scope
                     or t.scope.startswith(args.scope + ":")]
        if args.needs:
            tools = [t for t in tools if t.needs.upper() == args.needs.upper()]
        if args.core:
            tools = [t for t in tools if t.kind == "core"]
        if args.grep:
            pattern = re.compile(args.grep, re.I)
            tools = [t for t in tools
                     if pattern.search(t.name) or pattern.search(t.summary)]

    payload = [t.as_dict() for t in tools]

    def render():
        if not tools:
            ctx.out("nothing matched.")
            return
        width = max(len(t.name) for t in tools)
        for tool in tools:
            tag = ""
            if tool.needs and tool.needs.upper() not in ("-", "NONE", "ANY"):
                tag = ctx.out.paint(f" [{tool.needs}]", "grey")
            if tool.kind == "profile":
                tag += ctx.out.paint(" (profile)", "magenta")
            summary = tool.summary or ctx.out.paint("(undocumented)", "yellow")
            ctx.out(f"  {tool.name:<{width}}  {summary}{tag}")
        ctx.out.blank()
        ctx.out(f"{len(tools)} tools. "
                f"`porthole tools <name>` for one; `--json` for a machine.")

    return ctx.emit(payload, render)


def show(tool: Tool, ctx) -> int:
    """Print one tool's contract, without making the reader open the file."""
    if getattr(ctx.args, "json", False):
        return ctx.emit(tool.as_dict())
    ctx.out.heading(tool.name)
    if tool.summary:
        ctx.out(f"  {tool.summary}")
    ctx.out.blank()
    width = max((len(k) for k in tool.fields), default=6)
    for key in ("scope", "needs", "env", "exits", "gives", "device"):
        if key in tool.fields:
            ctx.out.kv(key, tool.fields[key], width)
    for gap in tool.gaps:
        ctx.out.kv(gap, ctx.out.paint("(not documented)", "yellow"), width)
    ctx.out.blank()
    ctx.out.kv("path", str(tool.path.relative_to(ctx.root)), width)
    ctx.out.blank()
    if tool.needs.upper() in ("BOOTED", "FASTBOOT"):
        ctx.out.hint(f"TK_AGENT=$USER tools/tk-device.sh "
                     f"--need-{tool.needs.lower()} {tool.path.relative_to(ctx.root)}")
    else:
        ctx.out.hint(f"{tool.path.relative_to(ctx.root)}")
    return EX_OK


SPEC = {
    "verb": "tools",
    "order": 25,
    "help": "search the toolbox and read a tool's contract",
    "description": (
        "Ninety-odd tools is more than anyone can hold in their head. Filter by\n"
        "scope, by the device state they need, or by name; then read one's\n"
        "contract without opening it.\n\n"
        "For agents: `porthole tools --json` is the catalogue to consult first."),
    "args": [
        (["name"], {"nargs": "?", "metavar": "ACTION|NAME",
                    "help": "list | lint | audit, or a tool name to read its contract"}),
        (["--scope"], {"metavar": "SCOPE",
                       "help": "generic | soc:<soc> | device:<codename>"}),
        (["--needs"], {"metavar": "STATE",
                       "help": "tools needing BOOTED, FASTBOOT, ..."}),
        (["--grep"], {"metavar": "REGEX", "help": "search names and summaries"}),
        (["--core"], {"action": "store_true",
                      "help": "exclude the active profile's tools"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_tools,
    "examples": [
        "porthole tools",
        "porthole tools --needs BOOTED",
        "porthole tools --grep suspend",
        "porthole tools tk-suspend-cycle.sh",
        "porthole tools lint",
        "porthole tools audit",
    ],
}
