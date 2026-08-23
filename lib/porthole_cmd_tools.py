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

FIELD_RE = re.compile(r"^#\s*(scope|needs|env|exits|gives|device):\s*(.*)$", re.M)
# The first comment line after the shebang that is not a field: the summary.
SUMMARY_RE = re.compile(r"^#\s*(\S.*?)\s*$", re.M)

REQUIRED_FIELDS = ("scope", "needs", "env", "exits")


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
        # Summary: the first comment line that is not the shebang, not a field,
        # not a divider. Tools already open with one by house style.
        for line in lines[:12]:
            stripped = line.strip()
            if not stripped.startswith("#") or stripped.startswith("#!"):
                continue
            body = stripped.lstrip("#").strip()
            if not body or set(body) <= set("-=# ") or FIELD_RE.match(stripped):
                continue
            self.summary = body
            break

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
        return [f for f in REQUIRED_FIELDS if f not in self.fields]

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


def collect(root: pathlib.Path, device: str = "") -> list[Tool]:
    paths = [p for p in sorted((root / "tools").iterdir())
             if p.is_file() and not p.is_symlink() and p.name != "__pycache__"]
    if device:
        pdir = root / "profiles" / device / "tools"
        if pdir.is_dir():
            paths += [p for p in sorted(pdir.iterdir())
                      if p.is_file() and not p.name.startswith(".")]
    return [Tool(p, root) for p in paths]


def cmd_tools(args, ctx) -> int:
    try:
        device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    except Exception:  # noqa: BLE001 -- listing must survive a broken profile
        device = ""
    tools = collect(ctx.root, device)

    if args.lint:
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
        (["name"], {"nargs": "?", "help": "show this tool's contract"}),
        (["--scope"], {"metavar": "SCOPE",
                       "help": "generic | soc:<soc> | device:<codename>"}),
        (["--needs"], {"metavar": "STATE",
                       "help": "tools needing BOOTED, FASTBOOT, ..."}),
        (["--grep"], {"metavar": "REGEX", "help": "search names and summaries"}),
        (["--core"], {"action": "store_true",
                      "help": "exclude the active profile's tools"}),
        (["--lint"], {"action": "store_true",
                      "help": "list tools with an incomplete header"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_tools,
    "examples": [
        "porthole tools",
        "porthole tools --needs BOOTED",
        "porthole tools --grep suspend",
        "porthole tools tk-suspend-cycle.sh",
        "porthole tools --lint",
    ],
}
