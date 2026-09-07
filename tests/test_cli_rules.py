#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The CLI rules, checked against the LIVE registry.

Every test here iterates `discover()` rather than naming verbs. That is the
whole point: a rule enforced verb by verb drifts the moment somebody adds a
tenth verb without reading the other nine, and the drift is invisible until a
user trips over it. Adding a verb that breaks a rule must fail here on the day
it is added, not in a bug report months later.

The rules and their motivating defects are in
docs/superpowers/specs/2026-08-24-cli-and-docs-design.md.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "porthole"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cli  # noqa: E402

class Skip(Exception):
    """This test could not run here.

    Reported as `skip`, never as a pass. A CI runner has no pmaports checkout,
    and a test that silently succeeds because its subject was absent is a test
    that will keep succeeding after the subject breaks.
    """


# Resolved ONCE, and handed to every subprocess below as PORTHOLE_PMAPORTS.
#
# The guard and the thing it guards have to look at the same environment. They
# did not: `run()` deliberately isolates XDG_CONFIG_HOME so the tester's own
# device selection cannot leak into an assertion, while `needs_pmaports()`
# asked the tester's real config -- so on a host whose pmaports is named by
# config.env rather than sitting in pmbootstrap's cache_git, the guard said
# "present", every subprocess said "no pmaports checkout found", and six tests
# failed for a reason that was nothing to do with what they test.
def _pmaports_path():
    import porthole
    import porthole_pmaports as pmap
    found = pmap.find_pmaports(porthole.load_config(root=ROOT))
    return str(found) if found else ""


PMAPORTS = _pmaports_path()


def needs_pmaports():
    if not PMAPORTS:
        raise Skip("no pmaports checkout on this host")


TMPXDG = tempfile.mkdtemp(prefix="porthole-rules-test-")
SPECS = porthole_cli.discover(ROOT)


def run(*args, env=None):
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "PORTHOLE_ROOT": str(ROOT), "XDG_CONFIG_HOME": TMPXDG,
            "NO_COLOR": "1"}
    # The one thing the isolated config cannot supply and several tests need.
    # Named explicitly rather than by letting config.env through: the point of
    # TMPXDG is that the tester's device selection stays out of these runs.
    if PMAPORTS:
        base["PORTHOLE_PMAPORTS"] = PMAPORTS
    base.update(env or {})
    proc = subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, env=base)
    return proc.returncode, proc.stdout, proc.stderr


def flags(spec):
    """Every option string a SPEC declares."""
    out = set()
    for names, _ in spec["args"]:
        out.update(n for n in names if n.startswith("-"))
    return out


def positionals(spec):
    return [names[0] for names, _ in spec["args"] if not names[0].startswith("-")]


# ------------------------------------------------------------------ R1 --

def test_device_flag_is_a_selector_only():
    """No verb may have BOTH an injected --device selector and a positional
    that names a device. That collision is what made
    `porthole new-device --device X` die in ProfileNotFound about the very
    profile it had been asked to create."""
    for spec in SPECS:
        names = positionals(spec)
        target = any(n in ("codename", "device", "device_pos") for n in names)
        if target:
            assert spec.get("device_flag") is False, (
                f"{spec['verb']} has a device-naming positional {names} but "
                f"still receives the --device selector")


def test_no_verb_declares_its_own_device_flag():
    """--device is injected, never declared. A verb declaring its own would
    shadow the global one and silently diverge from it."""
    for spec in SPECS:
        assert "--device" not in flags(spec), (
            f"{spec['verb']} declares --device; it is injected by with_device()")


def test_short_and_long_device_agree_before_and_after_the_verb():
    """`-d` used to work before the verb and not after -- the same papercut
    the --device fix was supposed to remove, wearing a hat."""
    for argv in (["-d", "google-taimen", "config", "PORTHOLE_DEVICE"],
                 ["config", "-d", "google-taimen", "PORTHOLE_DEVICE"],
                 ["--device", "google-taimen", "config", "PORTHOLE_DEVICE"],
                 ["config", "--device", "google-taimen", "PORTHOLE_DEVICE"]):
        rc, out, err = run(*argv)
        assert rc == 0, f"{argv} -> rc={rc} {err}"
        assert out.strip() == "google-taimen", f"{argv} -> {out!r}"


def test_new_device_rejects_the_selector_by_name():
    """The error must name the positional, not fail somewhere downstream."""
    rc, out, err = run("new-device", "--device", "google-cheetah")
    assert rc == 64, f"rc={rc}: {err}"
    assert "codename" in (out + err).lower()
    assert "ProfileNotFound" not in (out + err)


# ------------------------------------------------------------------ R2 --

ACTION_WORDS = {"list", "new", "lint", "submit", "reindex", "inherit", "diff",
                "status", "build", "serve", "show", "search"}


def test_actions_are_positional_not_flags():
    """A verb must not encode mutually exclusive MODES as boolean flags.

    The flag form permits `brain --new --lint --submit`, which is three
    actions at once and means nothing. A positional with `choices` makes that
    unrepresentable rather than merely discouraged."""
    for spec in SPECS:
        bad = {f.lstrip("-").replace("-", "_") for f in flags(spec)
               if f.startswith("--")} & ACTION_WORDS
        # A flag whose name is an action word is only wrong if it is a
        # store_true -- `--diff CODENAME` taking a value is a filter.
        offenders = set()
        for names, kw in spec["args"]:
            name = names[0].lstrip("-").replace("-", "_")
            if name in bad and kw.get("action") == "store_true":
                offenders.add(names[0])
        assert not offenders, (
            f"{spec['verb']} encodes actions as flags: {sorted(offenders)}; "
            f"use a positional ACTION with choices")

    # Positive control. No verb currently trips this rule, so without a
    # synthetic offender the assertion above can never fire and the test is
    # decoration. `brain/laws/every-test-needs-a-positive-control.md`.
    fake = {"verb": "fake", "args": [(["--lint"], {"action": "store_true"})]}
    offenders = {names[0] for names, kw in fake["args"]
                 if names[0].lstrip("-").replace("-", "_") in ACTION_WORDS
                 and kw.get("action") == "store_true"}
    assert offenders == {"--lint"}, "the rule no longer detects a flag-action"


# Verbs whose first positional is free text, so argparse `choices` cannot
# constrain it. They dispatch on a leading action word themselves, and must
# say something when that word is nearly-but-not an action -- otherwise a typo
# silently becomes a search and an empty result reads as "no such note".
FREE_TEXT_ACTION = {"brain", "tools"}


def test_multi_action_verbs_declare_choices():
    """An ACTION positional without `choices` gives no completion and no error
    for a typo. Checked by POSITION, not by the name `action`: hooking on the
    name silently skipped `brain` and `tools`, the two verbs converted to
    positional actions in the first place."""
    for spec in SPECS:
        if spec["verb"] in FREE_TEXT_ACTION:
            continue
        positional = [(names, kw) for names, kw in spec["args"]
                      if not names[0].startswith("-")]
        if not positional:
            continue
        names, kw = positional[0]
        if names[0] in ("action", "ACTION"):
            assert kw.get("choices"), (
                f"{spec['verb']}'s action positional has no choices")


def test_free_text_action_verbs_flag_a_near_miss():
    """`porthole brain lnit` must not silently become a search for "lnit"."""
    rc, out, err = run("brain", "lnit")
    assert "lint" in (out + err), f"no suggestion offered: {out + err!r}"


def test_soc_and_brain_take_positional_actions():
    needs_pmaports()
    for argv, needle in ((["soc", "list"], "device"),
                         # "note", not "notes": lint prints "0 note(s) with
                         # problems ... out of 64". The plural needle never
                         # matched, and needs_pmaports() skips this test on a
                         # runner -- so it passed in CI and failed on every
                         # machine that could actually run it.
                         (["brain", "lint"], "note"),
                         (["brain", "search", "--severity", "law"], "laws")):
        rc, out, err = run(*argv)
        assert rc == 0, f"{argv} -> rc={rc} {err}"
        assert needle in out.lower(), f"{argv} -> {out[:120]!r}"


def test_the_common_case_did_not_get_longer():
    """`porthole soc` and `porthole brain <query>` must still work: converting
    to positional actions is not a licence to make the frequent path verbose."""
    needs_pmaports()
    rc, out, err = run("brain", "watchdog")
    assert rc == 0 and "watchdog" in out.lower(), f"rc={rc} {err}"
    # `soc` with no action resolves the SoC from the selected device, so the
    # test must select one -- this harness runs with an empty XDG config.
    rc, out, err = run("-d", "google-taimen", "soc")
    assert rc == 0, f"rc={rc} {err}"
    assert "device" in out.lower(), f"{out[:120]!r}"


# ------------------------------------------------------------------ R3 --

def test_every_reporting_verb_takes_json():
    """`porthole --help` promises it. This makes the promise true."""
    for spec in SPECS:
        if spec.get("reports") is False:
            continue
        assert "--json" in flags(spec), (
            f"{spec['verb']} reports but has no --json; add it, or declare "
            f"reports=False if its stdout is not a report")


def test_json_output_actually_parses():
    needs_pmaports()
    for argv in (["devices", "--json"], ["soc", "list", "--json"],
                 ["brain", "search", "--severity", "law", "--json"],
                 ["tools", "--json"], ["tools", "audit", "--json"],
                 ["build", "--json"]):
        rc, out, err = run(*argv)
        assert rc == 0, f"{argv} -> rc={rc} {err}"
        json.loads(out)


def test_tools_audit_renders_plain():
    """render_audit had zero coverage. This does not assert on the findings
    (that is test_toolcontract.py's job against fixtures) -- only that the
    live tree renders without crashing."""
    rc, out, err = run("tools", "audit")
    assert rc == 0, f"rc={rc} {err}"
    assert out.strip(), "expected some report line"


# ------------------------------------------------------------------ R4 --

def test_verbs_escaping_their_scope_require_yes():
    for spec in SPECS:
        if spec.get("escapes_scope"):
            assert "--yes" in flags(spec), (
                f"{spec['verb']} writes outside its own profile and must "
                f"require --yes")


# ------------------------------------------------------------------ R5 --

def test_force_means_overwrite_not_dirty_tree():
    """`--force` is 'overwrite something that exists'. Refusing to branch over
    uncommitted work is not a file needing an overwrite, so that is
    `--allow-dirty`. One flag with two meanings is a flag nobody trusts."""
    for spec in SPECS:
        for names, kw in spec["args"]:
            if "--force" in names:
                help_text = (kw.get("help") or "").lower()
                for word in ("uncommitted", "dirty", "unclean"):
                    assert word not in help_text, (
                        f"{spec['verb']}'s --force mentions {word!r}; that "
                        f"meaning belongs to --allow-dirty")


# ------------------------------------------------------------------ R6 --

def test_unknown_soc_warns_and_still_scaffolds(tmp=None):
    """New silicon has no pmaports sibling BY DEFINITION. A rule that rejected
    unknown SoCs would reject exactly the ports this tool exists for."""
    needs_pmaports()
    import shutil
    dest = ROOT / "profiles" / "zzz-ruletest"
    shutil.rmtree(dest, ignore_errors=True)
    try:
        rc, out, err = run("new-device", "zzz-ruletest", "--soc",
                           "acme-notasoc9000", "--json")
        assert rc == 0, f"rc={rc}: {err}"
        payload = json.loads(out)
        assert payload["unknown_soc"] == "acme-notasoc9000"
        assert payload["seeded_from"] == []
        assert any("not used by any pmaports device" in n
                   for n in payload["notes"]), payload["notes"]
        assert payload["sibling_soc"] == "", payload
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_a_near_miss_soc_names_the_near_match_but_still_scaffolds():
    """A near-miss must NOT be fatal.

    A successor generation is one character from its predecessor BY DESIGN --
    google-gs201 vs google-gs101 -- so "looks like a typo" cannot tell a typo
    from the exact port this tool exists to start. What must be protected is
    seeding from the wrong silicon, and that is handled by not seeding at all
    (see the next test), not by refusing to run."""
    needs_pmaports()
    import shutil
    dest = ROOT / "profiles" / "zzz-ruletest2"
    shutil.rmtree(dest, ignore_errors=True)
    try:
        rc, out, err = run("new-device", "zzz-ruletest2", "--soc",
                           "qcom-msm899", "--json")
        assert rc == 0, f"a near-miss must not be fatal, rc={rc}: {err}"
        payload = json.loads(out)
        assert payload["seeded_from"] == [], payload
        assert any("msm8998" in n for n in payload["notes"]), payload["notes"]
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_an_unknown_soc_never_falls_back_to_vendor_inference():
    """Naming a SoC that does not exist must not quietly seed from whatever
    else that vendor makes. That fallback is for when NO SoC was named."""
    needs_pmaports()
    import shutil
    dest = ROOT / "profiles" / "zzz-ruletest3"
    shutil.rmtree(dest, ignore_errors=True)
    try:
        rc, out, err = run("new-device", "zzz-ruletest3", "--soc",
                           "acme-notasoc9000", "--json")
        assert rc == 0, f"rc={rc}: {err}"
        payload = json.loads(out)
        assert payload["seeded_from"] == [], payload
        assert payload["sibling_soc"] == "", payload
    finally:
        shutil.rmtree(dest, ignore_errors=True)


# ----------------------------------------------------------- housekeeping --

def test_every_spec_has_examples_and_help():
    for spec in SPECS:
        assert spec["help"], f"{spec['verb']} has no help line"
        assert spec["examples"], f"{spec['verb']} has no examples"


def test_completion_covers_every_verb():
    """Completion is generated from the registry, so this can only fail if a
    verb is registered in a way the generator cannot see."""
    for shell in ("bash", "zsh", "fish"):
        rc, out, err = run("completion", shell)
        assert rc == 0, f"{shell} -> {err}"
        for spec in SPECS:
            assert spec["verb"] in out, f"{spec['verb']} missing from {shell}"


def test_every_verb_declares_a_group():
    """35 verbs in one flat list is a wall, and `order` -- a bare integer --
    cannot say why `slots` sits between `brief` and `statusline`. The group is
    what the reader scans; the order is what sorts within it."""
    specs = porthole_cli.discover(ROOT)
    bad = [s["verb"] for s in specs
           if s.get("group") not in porthole_cli.GROUPS]
    assert not bad, (
        "these verbs declare no group, or one that is not in "
        f"{porthole_cli.GROUPS}:\n  " + "\n  ".join(sorted(bad)))


def test_the_help_lists_each_verb_once():
    """argparse's own positional dump and the hand-built epilog were both
    printed, so every verb appeared twice and the page ran to 111 lines."""
    rc, out, err = run("--help")
    assert rc == 0, err
    for verb in ("doctor", "build", "brain"):
        assert out.count("\n  " + verb + " ") + out.count("\n    " + verb + " ") == 1, (
            f"{verb} appears more than once in `porthole --help`:\n{out}")


def test_an_arg_group_never_reaches_add_argument():
    """`group` is porthole's key, not argparse's. If it is forwarded,
    add_argument raises TypeError and the verb disappears from the CLI --
    build() catches that and prints `skipping verb`, so the failure is a
    missing command rather than a crash."""
    specs = porthole_cli.discover(ROOT)
    parser, table = porthole_cli.build(ROOT, specs)
    missing = [s["verb"] for s in specs if s["verb"] not in table]
    assert not missing, f"these verbs failed to build: {missing}"


def test_a_grouped_flag_is_rendered_under_its_action():
    rc, out, err = run("aports", "--help")
    assert rc == 0, err
    # The test checks for actual argument group headings (a line that is only
    # the heading text), not substring matches. Substring matching would pass
    # on the old flat output where --soc's help text read "new: seed from...".
    lines = out.split("\n")
    headings_found = {line.strip() for line in lines if line.strip() in ("new:", "patches:")}
    assert headings_found == {"new:", "patches:"}, (
        "aports has 23 flags and 15 of them name their action in prose; "
        "they must be grouped in the parser too:\n" + out)


def test_a_hint_does_not_pad_its_own_column():
    """52 call sites hand-padded a command against an explanation and picked
    21 different widths between them, so the same `porthole doctor` hint lands
    in two different columns depending which verb printed it. The layout
    belongs to Out.hint, which is the only thing that can be consistent about
    it."""
    import io
    import porthole_cli

    # A run of two or more spaces INSIDE a hint string is a hand-built column.
    padded = []
    for path in sorted((ROOT / "lib").glob("porthole*.py")):
        for match in re.finditer(r"""\.hint\(\s*f?["']([^"']*)["']""",
                                 path.read_text()):
            if re.search(r"\S  +\S", match.group(1)):
                padded.append(f"{path.name}: {match.group(1)[:60]}")
    assert not padded, (
        "pass the explanation as hint()'s second argument instead:\n  "
        + "\n  ".join(padded))

    # ...and the second argument actually lines up.
    buf = io.StringIO()
    out = porthole_cli.Out(stream=buf, force_colour=False)
    out.hint("porthole doctor", "check the host and device")
    out.hint("porthole build ccache --max 25G", "raise it")
    lines = buf.getvalue().splitlines()
    at = [lines[0].index("check the host"), lines[1].index("raise it")]
    assert at[0] == at[1], f"two hints, two columns: {at}\n" + "\n".join(lines)

    # `{:<N}` pads UP TO N -- a command already >= N chars gets no separator
    # at all, and a note glued straight onto it (`google-taimenthe values...`)
    # is one unreadable token, worse than the ragged columns this fixes.
    # `porthole soc inherit google-taimen` is a real hint this repo prints,
    # and is exactly HINT_COLUMN (34) characters -- the boundary itself.
    buf2 = io.StringIO()
    out2 = porthole_cli.Out(stream=buf2, force_colour=False)
    command = "porthole soc inherit google-taimen"
    out2.hint(command, "the values worth copying")
    line = buf2.getvalue().splitlines()[0]
    command_end = line.index(command) + len(command)
    note_start = line.index("the values worth copying")
    assert note_start - command_end >= 1, (
        f"command and note need at least one space between them: {line!r}")


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
