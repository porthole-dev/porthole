# SPDX-License-Identifier: MIT
"""Documents: a tool's contract, a brain note, a milestone.

The biggest hole in the curses console was that there was no way to LOOK at anything.
`Enter` did nothing on the tools and brain panes even though both advertised it, so
reading a tool's contract or a note meant leaving curses entirely. 55 notes carrying the
laws of two ports were one keystroke away and unreachable.

These builders return DATA -- a title and some lines you scroll -- not drawing calls,
which is why they crossed from the curses pane almost unchanged and why they are testable
with no terminal at all. Whoever knows the subject builds the lines; one reader screen
renders them.

The help overlay used to live here too. It does not any more: help is generated from the
live Textual BINDINGS instead, because the curses version drifted from what the keys
actually did -- `/` was documented as "filter" in two panes while it opened the palette,
and the real filter key appeared in no footer at all. A help screen that reads the
bindings cannot disagree with them.
"""
from __future__ import annotations

import textwrap


class Doc:
    """A title, some lines, and optionally a command the reader may run."""

    __slots__ = ("title", "lines", "command", "safe")

    def __init__(self, title, lines, command="", safe=False):
        self.title, self.lines = title, list(lines)
        self.command, self.safe = command, safe


def wrap(text, width, indent=""):
    """Wrap a paragraph, preserving blank lines and pre-indented blocks."""
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
        elif para.startswith(("    ", "\t", "  - ", "  ")):
            out.append(para.rstrip())        # already laid out; leave it
        else:
            out.extend(textwrap.wrap(para, max(20, width),
                                     initial_indent=indent,
                                     subsequent_indent=indent) or [""])
    return out


# ------------------------------------------------------------------ builders --

def for_tool(root, name) -> Doc:
    """A tool's contract, the same facts `porthole tools <name>` prints."""
    import porthole_cmd_tools as tmod
    tools = [t for t in tmod.collect(root, "") if t.name == name]
    if not tools:
        return Doc(name, ["no such tool"])
    tool = tools[0]
    lines = [tool.summary or "(no summary)", ""]
    for label, value in (("scope", tool.scope), ("needs", tool.needs),
                         ("env", tool.fields.get("env", "")),
                         ("exits", tool.fields.get("exits", "")),
                         ("gives", tool.fields.get("gives", "")),
                         ("path", str(tool.path))):
        if value:
            lines.append(f"  {label:<7} {value}")
    needs = (tool.needs or "").upper()
    if needs and needs not in ("NONE", "ANY"):
        lines += ["", f"  This needs the device in {needs}. Take the mutex:",
                  f"    porthole run --lock {tool.name}"]
    return Doc(tool.name, lines, command=f"porthole run {tool.name}",
               safe=False)


def for_note(root, note_id) -> Doc:
    """A brain note, in full. These are the highest-value text in the repo and
    reading one used to cost a full-screen context switch."""
    import porthole_cmd_brain as bmod
    for note in bmod.load_notes(root):
        if note.id == note_id:
            head = [note.title, ""]
            for key in ("severity", "scope", "subsystem", "confidence",
                        "evidence"):
                if note.meta.get(key):
                    head.append(f"  {key:<11} {note.meta[key]}")
            return Doc(note.id, head + [""] + note.body.splitlines())
    return Doc(note_id, ["no such note"])


def for_milestone(row) -> Doc:
    """A milestone, and — when it is stale — the evidence that contradicts it.

    The evidence string is the whole answer for a stale milestone, and the port
    pane truncates it to fit a column.
    """
    lines = []
    state = row["state"].upper()
    if row["source"] == "stale":
        lines += ["TICKED, BUT THE TOOL DISAGREES", "",
                  f"  {row['evidence'] or 'the probe says this is not done'}",
                  "",
                  "A probe outranks a tick. Believing the box here would mean",
                  "believing this port is further along than it is.", ""]
    else:
        lines += [f"{state} ({row['source']})", ""]
        if row["evidence"]:
            lines += [f"  {row['evidence']}", ""]
    if row["why"]:
        lines += ["why", ""] + wrap(row["why"], 70, "  ") + [""]
    if row["how"]:
        lines += ["how", "", f"  {row['how']}", ""]
    if row["playbook"]:
        lines += ["read", "", f"  {row['playbook']}"]
    return Doc(row["title"], lines, command=row["how"], safe=row["safe"])
