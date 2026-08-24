# SPDX-License-Identifier: MIT
"""A scrollable reader, and the help overlay.

The biggest hole in the console was that there was no way to LOOK at anything.
`Enter` did nothing on the tools and brain panes even though both advertised it,
so reading a tool's contract or a note meant leaving curses entirely. 55 notes
carrying the laws of two ports were one keystroke away and unreachable.

One pager serves every case, because they are all the same shape: a title and
some lines you scroll. What differs is who builds the lines, and that belongs to
whoever knows the subject.
"""
from __future__ import annotations

import textwrap

from .. import theme as T


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


def for_help(panes) -> Doc:
    """Built from each pane's own keys(), which until now was dead code.

    Making the overlay read the panes means the footer, the panes and the help
    cannot disagree -- and they did: `/` was documented as "filter" in two
    panes while it actually opened the palette, and the real filter key was
    undiscoverable.
    """
    lines = ["Global", ""]
    for key, what in (("1-4", "switch pane"), ("tab", "next pane"),
                      ("/", "filter this list"), ("ctrl-p", "command palette"),
                      ("enter", "open / run the selection"),
                      ("j k ↑ ↓", "move"), ("pgup pgdn g G", "jump"),
                      ("esc", "close / cancel"), ("r", "refresh"),
                      ("?", "this help"), ("q", "quit")):
        lines.append(f"  {key:<14} {what}")
    for name, mod in panes:
        try:
            rows = mod.keys()
        except Exception:  # noqa: BLE001
            continue
        if rows:
            lines += ["", name, ""]
            lines += [f"  {k:<14} {v}" for k, v in rows]
    lines += ["", "Anything that flashes, writes to the device or touches a",
              "slot needs an explicit confirmation naming the command."]
    return Doc("keys", lines)


# ------------------------------------------------------------------- render --

def render(win, snap, height, width, sel=0, doc=None):
    if doc is None:
        return 0
    from . import _list
    body = [T.fit(l, max(10, width - 4)) for l in doc.lines]
    rows = max(1, height - 5)
    visible, offset, sel = _list.window(body, sel, rows)
    count = _list.scrollbar(offset, len(visible), len(body))

    win.addstr(1, 2, T.fit(doc.title, max(10, width - 20)),
               T.attr(T.ACTIVE, bold=True))
    if count:
        win.addstr(1, max(0, width - len(count) - 2), count, T.attr(T.DIM))
    for i, line in enumerate(visible):
        role = T.BASE
        stripped = line.strip()
        if stripped.isupper() and len(stripped) > 3:
            role = T.CRIT
        elif line and not line.startswith((" ", "\t")):
            role = T.NOTE
        win.addstr(3 + i, 2, line, T.attr(role))
    return len(body)


def keys():
    return [("esc", "back"), ("j k", "scroll"), ("r", "run it, if it has one")]
