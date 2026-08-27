#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
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

from porthole_cli import Bail

# findings is deliberately first. A trap says "do not do X"; a finding says
# "X is already answered, here is the answer and here is the theory it kills".
# Only the first kind was ever indexed, which is how a document that explicitly
# refuted two theories sat unread while both were re-derived over a day
# (docs/RETRO-2026-08-26.md item 14). Ordering is not cosmetic here: the whole
# failure was an answer ranked below the warnings.
SECTIONS = ("findings", "laws", "traps", "playbooks", "workflow", "devices")

# A deliberately small frontmatter reader. The alternative is a YAML dependency,
# and the frontmatter here is flat key: value by convention -- enforced by
# `porthole brain reindex`, which reports notes it could not parse.
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


ACTIONS = ("search", "new", "lint", "submit", "reindex")


def _rank(note) -> int:
    """Findings sort above everything else at equal relevance.

    Not a preference: an answer that already exists outranks a warning about
    the territory, and burying it under the warnings is the exact failure this
    section was added for.
    """
    return 0 if note.section == "findings" else 1


def cmd_brain(args, ctx) -> int:
    root = pathlib.Path(ctx.root)

    # `porthole brain watchdog` must keep working, so a first word that is not
    # an action is treated as the start of the query rather than an error. The
    # collision set is five words; a note whose id is literally "lint" can be
    # reached with `brain search lint`.
    action = "search"
    query = list(args.query or [])
    if query and query[0] in ACTIONS:
        action, query = query[0], query[1:]
    elif query:
        # A free-text query cannot use argparse `choices`, so a mistyped action
        # silently becomes a search for that typo. Searching is the right
        # fallback -- but say so, or the user reads an empty result as "no such
        # note" rather than "you typed the verb wrong".
        import difflib
        near = difflib.get_close_matches(query[0], ACTIONS, n=1, cutoff=0.75)
        if near:
            ctx.out.warn(f"searching for {query[0]!r}; did you mean "
                         f"`porthole brain {near[0]}`?")
    args.query = query

    if action == "new":
        return cmd_new(args, ctx, root)
    if action == "lint":
        return cmd_lint(args, ctx, root)
    if action == "submit":
        return cmd_submit(args, ctx, root)

    notes = load_notes(root)

    if action == "reindex":
        return write_index(root, notes)

    terms = [t.lower() for t in query]
    hits = []
    for note in notes:
        if not scope_matches(note.scope, args.scope or ""):
            continue
        if args.subsystem and note.meta.get("subsystem") != args.subsystem:
            continue
        if args.severity and note.meta.get("severity") != args.severity:
            continue
        # `refutes` is searched too: you look for the theory you are about to
        # re-derive, and the note that kills it is what you should find.
        haystack = (note.title + " " + note.id + " " + note.body + " "
                    + note.meta.get("refutes", "")).lower()
        # All terms must appear. Ranking by title hits first: a note whose
        # title matches is nearly always the one you meant.
        if terms and not all(t in haystack for t in terms):
            continue
        score = sum(2 for t in terms if t in (note.title + note.id).lower())
        # A hit in `refutes` is the strongest signal there is: it means this
        # note exists specifically to stop the idea you just typed.
        score += sum(3 for t in terms if t in note.meta.get("refutes", "").lower())
        hits.append((_rank(note), -score, note.section, note.id, note))

    hits.sort(key=lambda h: h[:4])

    if args.json:
        print(json.dumps([n.as_dict(root) for *_, n in hits], indent=2))
        return 0

    if not hits:
        print("nothing matched. Try fewer terms, or `porthole brain reindex` "
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
    findings = [n for *_, n in hits if n.section == "findings"]
    for *_, note in hits:
        print(f"  {note.section:<{swidth}}  {note.id:<{width}}  {note.title}")
    print(f"\n{len(hits)} notes. `porthole brain <id>` to read one.")
    if findings:
        # Said out loud rather than left to the section column. The point of a
        # finding is that it ends an investigation before it starts.
        n = len(findings)
        noun = "is a FINDING" if n == 1 else "are FINDINGS"
        it = "it" if n == 1 else "them"
        print(f"\n{n} of these {noun} -- a question already answered on this "
              f"port." if n == 1 else
              f"\n{n} of these {noun} -- questions already answered on this port.")
        print(f"Read {it} before forming a theory; that is what findings are for.")
    return 0


def write_index(root: pathlib.Path, notes: list[Note]) -> int:
    lines = [
        "# Brain index",
        "",
        "Generated by `porthole brain reindex`. Do not edit by hand.",
        "",
        "`scope` is the field that matters: filter to yours with",
        "`porthole brain search --scope soc:<yoursoc>`, which always includes the",
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


# ------------------------------------------------------- contribute --------

REQUIRED_FIELDS = ("id", "title", "scope", "subsystem", "severity",
                   "confidence", "evidence")
VALID_SCOPE = re.compile(r"^(generic|soc:[a-z0-9_-]+|device:[a-z0-9-]+)$")
VALID_SEVERITY = {"law", "trap", "technique", "fact", "finding"}
VALID_CONFIDENCE = {"proven", "probable", "suspected"}

TEMPLATE = """---
id: {id}
title: {title}
scope: {scope}
subsystem: {subsystem}
severity: {severity}
confidence: {confidence}
evidence: {evidence}
first-learned: {date}
---

**Symptom** — what it looks like when you hit it. Write this first and write it
well: people search by what they are seeing, not by the cause they do not know
yet.

**Cause** —

**What to do** —

<!--
Before you submit this, check it against the bar:

  - Would this have saved someone a session? If not, it is a note to yourself,
    not a note for the corpus.
  - Is `evidence:` something a stranger can re-check? A trap without a source
    is folklore, and folklore is what this corpus exists to replace.
  - Is `scope:` honest? Over-claiming portability is worse than scoping
    narrowly. If it only ever applied to one device, say so.
  - One idea per note. If the title needs an "and", it is two notes.

Link related notes with [[note-id]] -- liberally. A link to a note nobody has
written yet is a marker, not an error.
-->
"""


def _lint_note(note, index, root) -> list[str]:
    """Everything wrong with one note. Empty list means it passes."""
    problems = []
    rel = note.path.relative_to(root)

    for field in REQUIRED_FIELDS:
        value = note.meta.get(field, "")
        if not value:
            problems.append(f"missing `{field}:`")
        elif value.strip().upper().startswith("TODO"):
            # The scaffold seeds placeholders on purpose. Letting one through
            # would defeat the whole mechanism: a note whose evidence is the
            # word TODO is exactly the folklore this corpus replaces.
            problems.append(f"`{field}:` is still the scaffold placeholder")

    if note.meta.get("id") and note.meta["id"] != note.path.stem:
        problems.append(f"id {note.meta['id']!r} does not match the filename "
                        f"{note.path.stem!r}")

    scope = note.meta.get("scope", "")
    if scope and not VALID_SCOPE.match(scope):
        problems.append(f"scope {scope!r} is not generic | soc:<soc> | "
                        f"device:<codename>")

    sev = note.meta.get("severity", "")
    if sev == "finding":
        refutes = note.meta.get("refutes", "").strip()
        if not refutes or refutes.startswith("TODO"):
            problems.append(
                "a finding needs a `refutes:` line naming the theories it "
                "kills -- that is how someone about to re-derive one finds it")
    elif note.meta.get("refutes"):
        problems.append("`refutes:` belongs on a finding; this is a "
                        f"{sev or 'note'}")
    if sev and sev not in VALID_SEVERITY:
        problems.append(f"severity {sev!r} is not one of "
                        f"{', '.join(sorted(VALID_SEVERITY))}")

    conf = note.meta.get("confidence", "")
    if conf and conf not in VALID_CONFIDENCE:
        problems.append(f"confidence {conf!r} is not one of "
                        f"{', '.join(sorted(VALID_CONFIDENCE))}")

    # The bar the whole corpus rests on: a claim you cannot re-check is
    # folklore. `proven` in particular has to point at something specific.
    evidence = note.meta.get("evidence", "")
    if conf == "proven" and evidence and len(evidence) < 12:
        problems.append(f"confidence is `proven` but evidence is only "
                        f"{evidence!r} -- cite something a stranger can check")

    # A finding reports a measurement, and a measurement is only re-checkable
    # if the thing that produced it still exists. On 2026-08-27 a finding cited
    # cycler.py, slowcycle.sh and mash.sh for its numbers; none of the three had
    # ever been committed, so the next session had the conclusion, the counts,
    # and no way to reproduce any of it. Rebuilding the instrument took longer
    # than the fix did.
    #
    # Only findings, and only names that resolve NOWHERE in the repo: a note may
    # legitimately mention envkernel.sh or some upstream script it does not own.
    if sev == "finding":
        named = set(re.findall(r"\b([\w-]+\.(?:py|sh))\b",
                               evidence + " " + note.body))
        missing = sorted(n for n in named
                         if not any(root.rglob(n)) and not n.startswith("_"))
        if missing:
            problems.append(
                "names %s for its evidence, and %s not in the repo -- commit "
                "the instrument with the finding, or the numbers cannot be "
                "re-checked" % (", ".join(missing),
                                "it is" if len(missing) == 1 else "they are"))

    # An HTML comment is guidance to the author, not content. The scaffold
    # explains [[wikilinks]] inside one, and scanning it reports the example as
    # a dead link on every freshly created note.
    body_no_comments = re.sub(r"<!--.*?-->", "", note.body, flags=re.S)
    for link in re.findall(r"\[\[([a-z0-9/-]+)\]\]", body_no_comments):
        target = link.split("/")[-1]
        if target not in index:
            problems.append(f"[[{target}]] does not exist "
                            f"(fine as a marker; noted, not fatal)")

    if len(body_no_comments.strip()) < 80:
        problems.append("body is too short to be useful to anyone else")

    # An unedited scaffold is not a contribution. Catch it by its own prompts
    # rather than by length, because the prompts are long enough to pass a
    # length check on their own.
    for prompt in ("what it looks like when you hit it",
                   "**Cause** —\n\n**What to do** —"):
        if prompt in body_no_comments:
            problems.append("the scaffold prompts are still unanswered")
            break

    return problems


def cmd_lint(args, ctx, root) -> int:
    notes = load_notes(root)
    index = {n.id for n in notes} | {n.path.stem for n in notes}

    rows, fatal = [], 0
    for note in notes:
        problems = _lint_note(note, index, root)
        hard = [p for p in problems if "noted, not fatal" not in p]
        if problems:
            rows.append({"note": str(note.path.relative_to(root)),
                         "problems": problems, "fatal": bool(hard)})
        fatal += bool(hard)

    def render():
        o = ctx.out
        if not rows:
            o(o.paint(f"  all {len(notes)} notes pass", "green"))
            return
        for row in rows:
            colour = "red" if row["fatal"] else "yellow"
            o(f"  {o.paint(row['note'], colour)}")
            for problem in row["problems"]:
                o(f"      {problem}")
        o.blank()
        o(f"{fatal} note(s) with problems that must be fixed, "
          f"{len(rows) - fatal} with warnings, out of {len(notes)}.")

    ctx.emit({"notes": len(notes), "fatal": fatal, "rows": rows}, render)
    return 1 if fatal else 0


# A finding is shaped differently from a trap and must not borrow its template.
# A trap warns about territory: symptom, cause, what to do instead. A finding
# closes a question: here is what was asked, here is the answer, and here are
# the theories that are now dead. The `refutes:` line is the load-bearing part
# -- it is what makes the note findable by someone about to re-derive the very
# idea it killed.
FINDING_TEMPLATE = """---
id: {id}
title: {title}
scope: {scope}
subsystem: {subsystem}
severity: finding
confidence: {confidence}
evidence: {evidence}
refutes: {refutes}
first-learned: {date}
---

**The question** — what was actually being asked, in the words someone would
search for before they knew the answer.

**The answer** —

**What this rules out** — the theories that are dead, named plainly. Someone
about to spend a day on one of them should recognise it here and stop.

**How it was established** — the measurement, and what would overturn it.
"""


def cmd_new(args, ctx, root) -> int:
    import datetime

    note_id = args.query[0] if args.query else ""
    if not note_id:
        raise Bail("name the note", 64,
                   "porthole brain new a-short-kebab-case-id --severity trap")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", note_id):
        raise Bail(f"id must be lowercase kebab-case: {note_id!r}", 64,
                   "it becomes the filename and the [[link]] target")

    section = args.section or {"law": "laws", "trap": "traps",
                               "technique": "playbooks",
                               "finding": "findings",
                               "fact": "traps"}.get(args.severity or "trap",
                                                    "traps")
    target = pathlib.Path(root) / "brain" / section / f"{note_id}.md"
    if target.exists():
        raise Bail(f"{target.relative_to(root)} already exists", 1,
                   f"porthole brain {note_id}   to read it")

    target.parent.mkdir(parents=True, exist_ok=True)
    if (args.severity or "") == "finding" or section == "findings":
        target.write_text(FINDING_TEMPLATE.format(
            id=note_id,
            title=args.title or note_id.replace("-", " ").capitalize(),
            scope=args.scope or "generic",
            subsystem=args.subsystem or "TODO",
            confidence=args.confidence or "proven",
            evidence=args.evidence or "TODO: the measurement, that a stranger can re-check",
            refutes=args.refutes or "TODO: the theories this kills, comma separated",
            date=datetime.date.today().isoformat()))
        ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} "
                f"{target.relative_to(root)}")
        ctx.out.blank()
        ctx.out.hint(f"$EDITOR {target.relative_to(root)}")
        ctx.out.hint("fill in `refutes:` -- it is how someone finds this "
                     "before re-deriving it")
        ctx.out.hint("porthole brain lint            does it meet the bar")
        return 0

    target.write_text(TEMPLATE.format(
        id=note_id,
        title=args.title or note_id.replace("-", " ").capitalize(),
        scope=args.scope or "generic",
        subsystem=args.subsystem or "TODO",
        severity=args.severity or "trap",
        confidence=args.confidence or "proven",
        evidence=args.evidence or "TODO: what proves this, that a stranger can re-check",
        date=datetime.date.today().isoformat()))

    ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} "
            f"{target.relative_to(root)}")
    ctx.out.blank()
    ctx.out.hint(f"$EDITOR {target.relative_to(root)}")
    ctx.out.hint("porthole brain lint            does it meet the bar")
    ctx.out.hint("porthole brain submit          branch, commit, PR")
    return 0


def cmd_submit(args, ctx, root) -> int:
    """Turn a finished note into a branch, a commit and a pull request."""
    import subprocess

    root = pathlib.Path(root)

    def git(*a, check=False):
        proc = subprocess.run(["git", "-C", str(root), *a],
                              capture_output=True, text=True, timeout=60)
        if check and proc.returncode != 0:
            raise Bail(f"git {' '.join(a)}: {proc.stderr.strip()}", 1)
        return proc.returncode, proc.stdout.strip()

    _, porcelain = git("status", "--porcelain", "brain")
    changed = [l[3:] for l in porcelain.splitlines() if l.strip()]
    if not changed:
        raise Bail("no changes under brain/", 1,
                   "porthole brain new <id>   to start one")

    # Lint before proposing: a note that fails the bar should not become a PR
    # someone else has to reject.
    if cmd_lint(_LintArgs(), ctx, root) != 0:
        raise Bail("the linter found problems", 1,
                   "fix them, then submit -- a note that fails the bar wastes "
                   "a reviewer's time rather than saving it")

    ids = [pathlib.Path(c).stem for c in changed if c.endswith(".md")]
    topic = args.branch or f"brain/{ids[0] if ids else 'notes'}"
    subject = args.message or (
        f"brain: {ids[0].replace('-', ' ')}" if len(ids) == 1
        else f"brain: {len(ids)} notes")

    steps = [
        ("git switch -c " + topic, f"a branch for the note"),
        ("git add brain/", "stage it"),
        (f'git commit -s -m "{subject}"', "sign off (DCO)"),
        (f"git push -u origin {topic}", "publish the branch"),
        ('gh pr create --fill', "open the pull request"),
    ]

    if not args.yes:
        ctx.out.heading(f"submit {len(changed)} note change(s)")
        for c in changed:
            ctx.out(f"  {c}")
        ctx.out.blank()
        ctx.out.heading("this will run")
        for cmd, why in steps:
            ctx.out(ctx.out.paint(f"  {cmd}", "cyan") +
                    ctx.out.paint(f"   # {why}", "grey"))
        ctx.out.blank()
        ctx.out.hint("porthole brain submit --yes")
        return 0

    _, current = git("rev-parse", "--abbrev-ref", "HEAD")
    if current != topic:
        git("switch", "-c", topic, check=True)
    git("add", "brain", check=True)
    git("commit", "-s", "-m", subject, check=True)
    ctx.out(ctx.out.paint(f"  committed on {topic}", "green"))

    if args.no_push:
        ctx.out.hint(f"git push -u origin {topic}")
        return 0

    rc, _ = git("push", "-u", "origin", topic)
    if rc != 0:
        ctx.out.warn("push failed -- the commit is safe on the local branch")
        return 1
    ctx.out(ctx.out.paint(f"  pushed {topic}", "green"))

    import shutil as _shutil
    if _shutil.which("gh"):
        subprocess.run(["gh", "pr", "create", "--fill"], cwd=root)
    else:
        ctx.out.hint("open a pull request for " + topic +
                     "  (install `gh` to have this done for you)")
    return 0


class _LintArgs:
    """cmd_lint reads only these."""
    json = False


SPEC = {
    "verb": "brain",
    "order": 50,
    "help": "search the second brain",
    "description": (
        "Scoped notes: laws, traps, playbooks, workflow. --scope filters to\n"
        "what applies to your device and always includes the generic notes,\n"
        "because hiding the laws from someone who filtered would be backwards."),
    "args": [
        (["query"], {"nargs": "*", "metavar": "ACTION|WORD",
                     "help": "search | new | lint | submit | reindex, "
                             "then words to match or a note id"}),
        (["--scope"], {"metavar": "SCOPE",
                       "help": "generic | soc:<soc> | device:<codename>"}),
        (["--subsystem"], {"metavar": "NAME",
                           "help": "filter by, or set on a new note"}),
        (["--refutes"], {"metavar": "TEXT",
                         "help": "new: for a finding -- the theories it kills"}),
        (["--severity"], {"metavar": "LEVEL",
                          "help": "law | trap | technique | fact -- filter by, "
                                  "or set on a new note"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
        (["--title"], {"help": "new: the note's title"}),
        (["--confidence"], {"help": "new: proven | probable | suspected"}),
        (["--evidence"], {"help": "new: what proves it"}),
        (["--section"], {"help": "new: laws | traps | playbooks | workflow"}),
        (["--branch"], {"help": "submit: branch name"}),
        (["--message"], {"help": "submit: commit subject"}),
        (["--no-push"], {"action": "store_true", "help": "submit: commit only"}),
        (["--yes"], {"action": "store_true", "help": "submit: actually do it"}),
    ],
    "run": cmd_brain,
    "examples": [
        "porthole brain search --severity law",
        "porthole brain watchdog",
        "porthole brain search --scope soc:sdm845",
        "porthole brain new my-note-id --section traps",
        "porthole brain lint",
        "porthole brain reindex",
    ],
}
