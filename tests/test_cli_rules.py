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
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "porthole"
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cli  # noqa: E402

class Skip(Exception):
    """This test could not run here.

    Reported as `skip`, never as a pass. A CI runner has no pmaports checkout,
    and a test that silently succeeds because its subject was absent is a test
    that will keep succeeding after the subject breaks.
    """


def needs_pmaports():
    import porthole
    import porthole_pmaports as pmap
    if not pmap.find_pmaports(porthole.load_config(root=ROOT)):
        raise Skip("no pmaports checkout on this host")


TMPXDG = tempfile.mkdtemp(prefix="porthole-rules-test-")
SPECS = porthole_cli.discover(ROOT)


def run(*args, env=None):
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "PORTHOLE_ROOT": str(ROOT), "XDG_CONFIG_HOME": TMPXDG,
            "NO_COLOR": "1"}
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
                 ["tools", "--json"], ["tools", "audit", "--json"]):
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


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Skip as exc:
            skipped += 1
            print(f"  skip {name}: {exc}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{len(tests) - failed - skipped}/{len(tests)} passed{tail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
