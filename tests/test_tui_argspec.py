#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""argparse specs -> form fields -> a command string.

The table-driven tests at the bottom are the ones that matter: they run over
every verb the registry discovers, so a verb whose arguments cannot be rendered
fails CI the day it lands rather than the day someone opens its form. Same
property the glob registry already gives `porthole --help`.

Pure. No terminal, no textual.
"""
import pathlib
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cli  # noqa: E402
from porthole_tui import argspec  # noqa: E402

BLOBS = {
    "verb": "blobs",
    "help": "get at vendor firmware during bring-up, without root",
    "description": "pmOS packages firmware by fetching it in the aport.",
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["unsparse", "unpack", "ls", "extract", "inventory"],
                      "help": "unsparse | unpack | ls | extract | inventory"}),
        (["path"], {"nargs": "?", "help": "the image or archive"}),
        (["--out"], {"metavar": "PATH", "help": "output file or directory"}),
        (["--dir"], {"metavar": "PATH", "help": "directory inside the image"}),
        (["--match"], {"metavar": "REGEX", "help": "extract: only these names"}),
        (["--dry-run"], {"action": "store_true", "dest": "dry_run",
                         "help": "unpack: list what is inside, extract nothing"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "examples": ["porthole blobs unsparse vendor.img --out vendor.raw.img"],
}


def by_dest(fs):
    return {f.dest: f for f in fs}


def test_choices_become_a_select_carrying_its_help():
    f = by_dest(argspec.fields(BLOBS))["action"]
    assert f.kind == "select", f.kind
    assert f.choices == ["unsparse", "unpack", "ls", "extract", "inventory"]
    assert "unsparse" in f.help


def test_store_true_becomes_a_checkbox():
    assert by_dest(argspec.fields(BLOBS))["dry_run"].kind == "check"


def test_a_path_metavar_gets_a_browse_button():
    fs = by_dest(argspec.fields(BLOBS))
    assert fs["out"].kind == "path" and fs["out"].browse
    assert fs["dir"].kind == "path" and fs["dir"].browse


def test_a_positional_named_path_gets_a_browse_button():
    # No metavar at all -- inferred from the dest and the help text. This is
    # the exact field the reported `vendor` problem could not reach.
    f = by_dest(argspec.fields(BLOBS))["path"]
    assert f.kind == "path", f.kind
    assert f.browse


def test_a_plain_option_stays_text():
    assert by_dest(argspec.fields(BLOBS))["match"].kind == "text"


def test_json_is_not_offered():
    # --json exists for agents and pipes. Inside the console it is noise, and
    # an option that does nothing visible is worse than an absent one.
    assert "json" not in by_dest(argspec.fields(BLOBS))


def test_build_assembles_positionals_in_order_then_flags():
    fs = argspec.fields(BLOBS)
    cmd = argspec.build("blobs", fs, {
        "action": "unsparse", "path": "~/dl/vendor.img",
        "out": "vendor.raw.img", "dry_run": False})
    assert cmd == "porthole blobs unsparse ~/dl/vendor.img --out vendor.raw.img", cmd


def test_build_omits_empty_values_and_false_flags():
    fs = argspec.fields(BLOBS)
    cmd = argspec.build("blobs", fs, {"action": "inventory", "path": "",
                                      "out": "", "dry_run": False})
    assert cmd == "porthole blobs inventory", cmd


def test_build_includes_a_true_flag():
    fs = argspec.fields(BLOBS)
    cmd = argspec.build("blobs", fs, {"action": "unpack", "path": "f.zip",
                                      "dry_run": True})
    assert cmd == "porthole blobs unpack f.zip --dry-run", cmd


def test_build_quotes_a_value_with_spaces():
    fs = argspec.fields(BLOBS)
    cmd = argspec.build("blobs", fs, {"action": "ls", "path": "/tmp/my image.img"})
    assert cmd == "porthole blobs ls '/tmp/my image.img'", cmd


def test_render_leaves_tilde_readable_but_quotes_anything_unsafe():
    # `~` stays bare so the previewed command reads like something a human typed;
    # jobs.py expands it before exec. Everything shell-unsafe is still quoted.
    fs = argspec.fields(BLOBS)
    assert argspec.build("blobs", fs, {"action": "ls", "path": "~/dl/vendor.img"}) \
        == "porthole blobs ls ~/dl/vendor.img"
    for hostile in ("a;rm -rf /", "$(evil)", "`evil`", "a b"):
        built = argspec.build("blobs", fs, {"action": "ls", "path": hostile})
        assert built == "porthole blobs ls " + shlex.quote(hostile), built


def test_slot_fields_are_marked_dangerous():
    spec = {"verb": "flash", "args": [(["--slot"], {"help": "which slot"})]}
    assert by_dest(argspec.fields(spec))["slot"].dangerous


def test_needs_arguments_distinguishes_the_two_kinds_of_verb():
    assert argspec.needs_arguments(BLOBS)
    assert not argspec.needs_arguments({"verb": "brief", "args": []})


# ------------------------------------------------------ the ones that matter --

def test_every_discovered_verb_renders_and_builds():
    """Table-driven over the real registry.

    A verb whose args cannot be rendered fails here on the day it lands. The
    old palette built `porthole <verb>` for all 29 and could reach none of
    their subcommands; this asserts the replacement can.
    """
    specs = porthole_cli.discover(ROOT)
    assert len(specs) > 20, "registry looks empty: {}".format(len(specs))
    for spec in specs:
        fs = argspec.fields(spec)
        for f in fs:
            assert f.kind in argspec.KINDS, "{}.{}: {}".format(
                spec["verb"], f.dest, f.kind)
            assert f.dest, "{}: a field with no dest".format(spec["verb"])
            assert f.label, "{}.{}: no label".format(spec["verb"], f.dest)
        cmd = argspec.build(spec["verb"], fs, {})
        assert cmd == "porthole " + spec["verb"], cmd


def test_every_documented_example_round_trips():
    """Each direct `porthole <verb> ...` example must decompose back into field values.

    Examples are the form's "try:" rows: pressing one back-fills every field. If
    an example cannot be decomposed, that row would silently do nothing.

    Examples that document SHELL usage rather than a verb invocation are
    excluded -- `cd "$(porthole cd)"` and `porthole completion bash > file` are
    instructions for a shell, and the form must not offer them at all.
    """
    direct = snippets = 0
    for spec in porthole_cli.discover(ROOT):
        fs = argspec.fields(spec)
        for example in spec.get("examples", []):
            if not argspec.is_direct(example, spec["verb"]):
                snippets += 1
                continue
            direct += 1
            values = argspec.parse_example(example, spec["verb"], fs)
            assert values is not None, "{}: cannot decompose {!r}".format(
                spec["verb"], example)
    assert direct > 90, "expected ~102 direct invocations, got {}".format(direct)
    assert snippets < 15, "too many examples excluded as shell snippets: {}".format(snippets)


def test_a_globally_injected_flag_does_not_leak_its_value():
    # -d/--device is injected by porthole_cli.with_device() into every verb, so it
    # is in no verb's args. Skipping the flag without consuming its value would
    # land the codename in the first positional.
    spec = {"verb": "tui", "args": []}
    assert argspec.parse_example("porthole tui -d google-cheetah", "tui",
                                 argspec.fields(spec)) == {}


def test_a_trailing_comment_is_not_an_argument():
    spec = {"verb": "brief", "args": [
        (["--no-device"], {"action": "store_true", "help": "skip the probe"})]}
    fs = argspec.fields(spec)
    assert argspec.parse_example("porthole brief --no-device    # offline", "brief", fs) \
        == {"no_device": True}


def test_a_variadic_positional_absorbs_its_tokens_and_rebuilds():
    spec = {"verb": "brain", "args": [
        (["query"], {"nargs": "*", "metavar": "ACTION|WORD"}),
        (["--section"], {}),
    ]}
    fs = argspec.fields(spec)
    values = argspec.parse_example(
        "porthole brain new my-note-id --section traps", "brain", fs)
    assert values == {"query": "new my-note-id", "section": "traps"}, values
    # and it must rebuild as SEPARATE argv tokens, not one quoted blob
    assert argspec.build("brain", fs, values) \
        == "porthole brain new my-note-id --section traps"


def test_shell_snippets_are_recognised_and_excluded():
    assert not argspec.is_direct('cd "$(porthole cd)"', "cd")
    assert not argspec.is_direct("porthole completion bash > ~/x", "completion")
    assert argspec.is_direct("porthole blobs ls vendor.img", "blobs")
    # a pipe INSIDE a quoted argument is not a shell operator
    assert argspec.is_direct("porthole blobs extract v.img --match 'wlan|bdwlan'", "blobs")


def test_interactive_is_a_bool_for_every_verb():
    for spec in porthole_cli.discover(ROOT):
        assert spec.get("interactive", False) in (True, False), spec["verb"]


def test_serial_declares_itself_interactive():
    # It drives termios directly. Streaming it into a drawer would mangle it,
    # and a pty here would be a second terminal emulator in a project that
    # already has one.
    specs = {s["verb"]: s for s in porthole_cli.discover(ROOT)}
    assert specs["serial"].get("interactive") is True


def test_is_interactive_reads_the_registry():
    from porthole_tui import jobs
    assert jobs.is_interactive(ROOT, "porthole serial console")
    assert not jobs.is_interactive(ROOT, "porthole brief")
    assert not jobs.is_interactive(ROOT, "porthole")
    assert not jobs.is_interactive(ROOT, "sh -c true")


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL {}:\n  {}".format(name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("ERROR {}: {}: {}".format(name, type(exc).__name__, exc))
    print("{}/{} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
