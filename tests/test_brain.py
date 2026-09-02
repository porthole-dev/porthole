#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The brain: findings, and the drift that made laws invisible.

Two defects motivate this file, both from docs/RETRO-2026-08-26.md.

`porthole brief` restated the rules in a hand-written list while `brain/laws/`
grew to ten notes. It showed three of them. Writing a law therefore had no
effect on anything an agent was ever shown -- including "read the vendor before
inventing a mechanism", which cost a day the week it was added.

And the brain indexed traps ("do not do X") but had no shape for findings ("X
is already answered"). A document that explicitly refuted two theories sat
unread while both were re-derived.

Both are drift bugs: the knowledge existed and the delivery did not. Tests here
assert the delivery, not the knowledge.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "porthole"
sys.path.insert(0, str(ROOT / "lib"))

TMPXDG = tempfile.mkdtemp(prefix="porthole-brain-test-")


def run(*args, env=None):
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "PORTHOLE_ROOT": str(ROOT), "XDG_CONFIG_HOME": TMPXDG,
            "NO_COLOR": "1", "TK_DEVICE_STATE": "ABSENT"}
    base.update(env or {})
    p = subprocess.run([sys.executable, str(CLI), *args],
                       capture_output=True, text=True, env=base)
    return p.returncode, p.stdout, p.stderr


def law_titles():
    import porthole_cmd_brain as B
    return [n.title for n in B.load_notes(ROOT)
            if n.meta.get("severity") == "law"]


# ------------------------------------------------------- the drift that bit --

def test_the_brief_shows_every_law_not_a_hand_written_subset():
    """The defect exactly: ten laws in brain/, three in the brief.

    Iterating the real notes rather than naming them is the point -- a test
    that listed the laws would drift the same way the brief did.
    """
    rc, out, err = run("-d", "google-taimen", "brief", "--no-device")
    assert rc == 0, err
    missing = [t for t in law_titles() if t not in out]
    assert not missing, f"the brief omits {len(missing)} law(s): {missing}"


def test_the_brief_json_carries_the_laws_and_findings():
    rc, out, err = run("-d", "google-taimen", "brief", "--no-device", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["laws"], "no laws in the brief payload"
    assert len(payload["laws"]) == len(law_titles())
    assert "findings" in payload, "findings key absent from the payload"


# ------------------------------------------------------------- findings --

def test_a_finding_ranks_above_a_trap_for_the_same_query():
    """A trap warns about territory; a finding ends the question. Burying the
    answer under the warnings is the failure this section exists for."""
    rc, out, err = run("brain", "UCM")
    assert rc == 0, err
    lines = [l for l in out.splitlines() if l.strip().startswith(("findings", "traps"))]
    assert lines, f"expected findings and traps in:\n{out}"
    assert lines[0].strip().startswith("findings"), \
        f"a trap outranked a finding:\n{out}"


def test_a_findings_hit_is_announced_not_left_in_a_column():
    rc, out, _ = run("brain", "UCM")
    assert rc == 0
    assert "FINDING" in out, "a findings hit was not called out"
    assert "before forming a theory" in out


def test_refutes_is_searchable_so_a_dead_theory_finds_its_obituary():
    """You search for the thing you are about to re-derive."""
    for theory in ("PulseAudio", "loudspeaker routing"):
        rc, out, _ = run("brain", *theory.split())
        assert rc == 0, f"{theory!r} matched nothing"
        assert "call-audio" in out, \
            f"searching {theory!r} did not surface the finding that refutes it"


def test_a_finding_without_refutes_fails_lint():
    """Positive control: the rule must be able to fail, or it proves nothing."""
    import porthole_cmd_brain as B
    d = pathlib.Path(tempfile.mkdtemp(prefix="brain-lint-"))
    (d / "brain" / "findings").mkdir(parents=True)
    note = d / "brain" / "findings" / "x.md"
    body = ("---\nid: x\ntitle: X\nscope: generic\nsubsystem: audio\n"
            "severity: finding\nconfidence: proven\nevidence: measured\n"
            "first-learned: 2026-01-01\n---\n\nbody\n")
    note.write_text(body)
    parsed = B.parse_note(note)
    problems = B._lint_note(parsed, {}, d)
    assert any("refutes" in p for p in problems), \
        f"a finding with no refutes passed lint: {problems}"

    note.write_text(body.replace("first-learned:",
                                 "refutes: it is the sound server\nfirst-learned:"))
    parsed = B.parse_note(note)
    assert not [p for p in B._lint_note(parsed, {}, d) if "refutes" in p], \
        "a finding WITH refutes was still rejected"


def test_refutes_on_a_non_finding_is_rejected():
    import porthole_cmd_brain as B
    d = pathlib.Path(tempfile.mkdtemp(prefix="brain-lint2-"))
    (d / "brain" / "traps").mkdir(parents=True)
    note = d / "brain" / "traps" / "y.md"
    note.write_text("---\nid: y\ntitle: Y\nscope: generic\nsubsystem: audio\n"
                    "severity: trap\nconfidence: proven\nevidence: measured\n"
                    "refutes: something\nfirst-learned: 2026-01-01\n---\n\nbody\n")
    problems = B._lint_note(B.parse_note(note), {}, d)
    assert any("refutes" in p for p in problems), problems


def test_brain_new_finding_lands_in_findings_with_its_own_template():
    """A finding must not borrow the trap template -- the shapes differ."""
    rc, out, err = run("brain", "new", "a-test-finding", "--severity", "finding",
                       "--refutes", "the old theory")
    try:
        assert rc == 0, err
        made = ROOT / "brain" / "findings" / "a-test-finding.md"
        assert made.is_file(), out
        text = made.read_text()
        assert "refutes: the old theory" in text
        assert "**The question**" in text and "**What this rules out**" in text
        assert "**Symptom**" not in text, "it used the trap template"
    finally:
        (ROOT / "brain" / "findings" / "a-test-finding.md").unlink(missing_ok=True)


def _lint_a_finding(body_extra, evidence):
    """Write one finding into the repo, lint it, remove it. Returns the
    complaint about instruments, or "" if there was none."""
    note = ROOT / "brain" / "findings" / "zz-instrument-probe.md"
    note.write_text(
        "---\n"
        "id: zz-instrument-probe\n"
        "title: A probe for the instrument rule\n"
        "scope: generic\n"
        "subsystem: build\n"
        "severity: finding\n"
        "confidence: proven\n"
        f"evidence: \"{evidence}\"\n"
        "refutes: \"nothing; this is a lint probe\"\n"
        "first-learned: 2026-08-29\n"
        "---\n\n"
        "**The question** — does the instrument rule fire?\n\n"
        f"**The answer** — {body_extra}\n")
    try:
        out = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "porthole"), "brain", "lint"],
            capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "commit the instrument" in line:
                return line.strip()
        return ""
    finally:
        note.unlink(missing_ok=True)


def test_a_bare_instrument_name_is_still_refused():
    """The guard exists because a finding once cited three scripts that had
    never been committed, leaving the next session the conclusion and no way
    to reproduce it."""
    complaint = _lint_a_finding("measured with zzprobe.py, which is nowhere.",
                                "measured with zzprobe.py across 40 runs")
    assert "zzprobe.py" in complaint, (
        "a finding citing an uncommitted instrument was accepted: " + repr(complaint))


def test_an_upstream_file_named_with_its_path_is_accepted():
    """A note may legitimately point at a file in ANOTHER project. Qualifying
    it with a directory says where it lives, which is what makes it
    re-checkable -- a bare basename does not."""
    complaint = _lint_a_finding(
        "see other/proj/zzprobe.py, which lives upstream.",
        "read of other/proj/zzprobe.py at v1.2.3")
    assert complaint == "", (
        "a deliberately qualified upstream path was refused: " + complaint)



# ------------------------------------------- a note is not part of your work --

class _Out:
    """Only what cmd_submit reaches for."""
    def __init__(self): self.lines = []
    def __call__(self, text): self.lines.append(text)
    def paint(self, text, _colour): return text
    def heading(self, text): self.lines.append(text)
    def hint(self, text): self.lines.append(text)
    def warn(self, text): self.lines.append(text)
    def blank(self): pass


class _Ctx:
    def __init__(self): self.out = _Out()


class _SubmitArgs:
    branch = "brain/a-test-note"
    message = "brain: a test note"
    no_push = True
    yes = True


def test_a_note_branches_off_main_and_puts_the_checkout_back():
    """#41: `git switch -c` with no start point branches from HEAD, and this is
    a SHARED checkout. A submit run while another agent had it on a feature
    branch cut the note from that branch -- so five brain files arrived as a PR
    carrying three unrelated commits and its red CI -- and left the checkout
    there, mid-task, for the other agent to undo by hand.

    Real git, because the defect is entirely in what git was asked to do.
    """
    import porthole_cmd_brain as B

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="brain-submit-"))
    origin, work = tmp / "origin.git", tmp / "work"

    def g(*a, cwd=work):
        return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True,
                              text=True, check=True).stdout.strip()

    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)],
                   check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    for k, v in (("user.name", "T"), ("user.email", "t@e"), ("commit.gpgsign", "false")):
        g("config", k, v)
    (work / "README").write_text("x\n")
    g("add", "README"); g("commit", "-qm", "base")
    g("remote", "add", "origin", str(origin))
    g("push", "-q", "origin", "main")
    main_tip = g("rev-parse", "main")

    # Somebody else's work in progress, checked out right now.
    g("switch", "-qc", "feature")
    (work / "OTHER").write_text("y\n")
    g("add", "OTHER"); g("commit", "-qm", "someone else's commit")

    (work / "brain" / "traps").mkdir(parents=True)
    (work / "brain" / "traps" / "a-test-note.md").write_text("body\n")

    lint = B.cmd_lint
    B.cmd_lint = lambda *a, **k: 0          # linting has its own tests above
    try:
        assert B.cmd_submit(_SubmitArgs(), _Ctx(), work) == 0
    finally:
        B.cmd_lint = lint

    assert g("rev-parse", "brain/a-test-note^") == main_tip, \
        "the note was cut from the feature branch, not from origin/main"
    assert g("rev-parse", "--abbrev-ref", "HEAD") == "feature", \
        "the submit left the shared checkout on the note's branch"
    assert not (work / "brain").exists(), \
        "the note is still in the other branch's working tree"

def test_the_index_is_current():
    """A note that is not in the index is a note nobody finds.

    Eight commits added a brain note without touching brain/INDEX.md -- the
    reindex is a separate command and remembering it is not a mechanism. The
    index is generated, so "is it current" is a comparison, not a judgement,
    and there is no reason for a human to be the one making it.

    Failing here means: run `make brain-index` and commit the result.
    """
    import porthole_cmd_brain as B
    notes = B.load_notes(ROOT)
    want = B.render_index(ROOT, notes)
    have = (ROOT / "brain" / "INDEX.md").read_text(encoding="utf-8")
    if want == have:
        return
    # Only sections the index actually renders. brain/memory/ is not one, so
    # a hint computed over every note names a file that is absent on purpose
    # -- a diagnostic that sends you to the wrong file is worse than none.
    indexed = [n for n in notes if n.section in B.SECTIONS]
    missing = [n.id for n in indexed if f"[{n.id}](" not in have]
    detail = ("\n%d note(s) are not in it: %s"
              % (len(missing), ", ".join(sorted(missing)[:8]))) if missing else ""
    assert False, ("brain/INDEX.md is stale -- run `make brain-index` and "
                   "commit the result." + detail)


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
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
