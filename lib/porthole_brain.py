#!/usr/bin/env python3
"""`porthole brain` -- search the second brain, and regenerate its index.

The brain is plain markdown on purpose: any agent, any harness, `grep`, and a
human all read it the same way. This module exists only to make it *findable*.
Nothing here is required to use the corpus.

Scope filtering is the feature that matters. A developer on a Snapdragon 845
device wants the laws and the generic traps and none of the msm8998 register
trivia, and `--scope` is how they get exactly that.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

SECTIONS = ("laws", "traps", "playbooks", "workflow", "devices")

# A deliberately small frontmatter reader. The alternative is a YAML dependency,
# and the frontmatter here is flat key: value by convention -- enforced by
# `porthole brain --reindex`, which reports notes it could not parse.
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)


class Note:
    __slots__ = ("path", "meta", "body")

    def __init__(self, path: pathlib.Path, meta: dict, body: str):
        self.path, self.meta, self.body = path, meta, body

    @property
    def id(self) -> str:
        return self.meta.get("id") or self.path.stem

    @property
    def title(self) -> str:
        return self.meta.get("title", self.id)

    @property
    def scope(self) -> str:
        return self.meta.get("scope", "generic")

    @property
    def section(self) -> str:
        return self.path.parent.name

    def as_dict(self, root: pathlib.Path) -> dict:
        return {"id": self.id, "title": self.title, "scope": self.scope,
                "subsystem": self.meta.get("subsystem", ""),
                "severity": self.meta.get("severity", ""),
                "confidence": self.meta.get("confidence", ""),
                "evidence": self.meta.get("evidence", ""),
                "path": str(self.path.relative_to(root))}


def parse_note(path: pathlib.Path) -> Note | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = FM_RE.match(text)
    if not m:
        return Note(path, {}, text)
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return Note(path, meta, m.group(2))


def load_notes(root: pathlib.Path) -> list[Note]:
    brain = root / "brain"
    notes = []
    for path in sorted(brain.rglob("*.md")):
        if path.name in ("INDEX.md", "README.md") and path.parent == brain:
            continue
        note = parse_note(path)
        if note:
            notes.append(note)
    return notes


def scope_matches(note_scope: str, want: str) -> bool:
    """`--scope soc:msm8998` means "what applies to me", not "exactly this".

    A device-specific worker needs the generic laws too, so a filter always
    includes generic. Anything else would make the filter actively harmful:
    hiding the laws from someone filtering to their device is the opposite of
    what they asked for.
    """
    if not want:
        return True
    if note_scope == "generic":
        return True
    return note_scope == want or note_scope.startswith(want + ":")


def cmd_brain(args, root: pathlib.Path) -> int:
    root = pathlib.Path(root)
    notes = load_notes(root)

    if args.reindex:
        return write_index(root, notes)

    terms = [t.lower() for t in (args.query or [])]
    hits = []
    for note in notes:
        if not scope_matches(note.scope, args.scope or ""):
            continue
        if args.subsystem and note.meta.get("subsystem") != args.subsystem:
            continue
        if args.severity and note.meta.get("severity") != args.severity:
            continue
        haystack = (note.title + " " + note.id + " " + note.body).lower()
        # All terms must appear. Ranking by title hits first: a note whose
        # title matches is nearly always the one you meant.
        if terms and not all(t in haystack for t in terms):
            continue
        score = sum(2 for t in terms if t in (note.title + note.id).lower())
        hits.append((-score, note.section, note.id, note))

    hits.sort(key=lambda h: h[:3])

    if args.json:
        print(json.dumps([n.as_dict(root) for *_, n in hits], indent=2))
        return 0

    if not hits:
        print("nothing matched. Try fewer terms, or `porthole brain --reindex` "
              "then read brain/INDEX.md")
        return 1

    # One hit: print it. That is nearly always what was wanted, and making the
    # caller run a second command to read the answer is a papercut.
    if len(hits) == 1:
        note = hits[0][-1]
        print(f"# {note.title}\n")
        print(f"  {note.path.relative_to(root)}  [{note.scope}]\n")
        print(note.body.strip())
        return 0

    # Ranked, not grouped: relevance order interleaves sections, so the
    # section goes in a column rather than a heading that would repeat.
    width = max(len(n.id) for *_, n in hits)
    swidth = max(len(n.section) for *_, n in hits)
    for *_, note in hits:
        print(f"  {note.section:<{swidth}}  {note.id:<{width}}  {note.title}")
    print(f"\n{len(hits)} notes. `porthole brain <id>` to read one.")
    return 0


def write_index(root: pathlib.Path, notes: list[Note]) -> int:
    lines = [
        "# Brain index",
        "",
        "Generated by `porthole brain --reindex`. Do not edit by hand.",
        "",
        "`scope` is the field that matters: filter to yours with",
        "`porthole brain --scope soc:<yoursoc>`, which always includes the",
        "generic notes as well.",
        "",
    ]
    unparsed = [n for n in notes if not n.meta.get("id")]
    for section in SECTIONS:
        chosen = [n for n in notes if n.path.parent.name == section
                  or n.path.parent.parent.name == section]
        if not chosen:
            continue
        lines += [f"## {section}", ""]
        lines += ["| id | scope | title |", "|---|---|---|"]
        for note in sorted(chosen, key=lambda n: n.id):
            rel = note.path.relative_to(root / "brain")
            lines.append(f"| [{note.id}]({rel}) | `{note.scope}` | {note.title} |")
        lines.append("")

    counts = {}
    for note in notes:
        counts[note.scope] = counts.get(note.scope, 0) + 1
    lines += ["## By scope", ""]
    for scope, count in sorted(counts.items()):
        lines.append(f"- `{scope}` — {count}")
    lines.append("")

    (root / "brain" / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote brain/INDEX.md — {len(notes)} notes")
    if unparsed:
        # Not fatal: a note with no frontmatter is still readable. But it will
        # not be findable by scope, which is the whole point of the filter.
        print(f"warning: {len(unparsed)} note(s) have no parseable frontmatter "
              f"and cannot be scope-filtered:", file=sys.stderr)
        for note in unparsed:
            print(f"  {note.path.relative_to(root)}", file=sys.stderr)
    return 0
