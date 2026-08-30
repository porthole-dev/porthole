#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What the installed pmbootstrap actually supports.

porthole shells pmbootstrap for seven things. Two of them had silently
disappeared by 3.11.1 -- `lint` and `config channel` -- and porthole reported
the resulting tool failure as a finding about the user's packages. This suite
is the guard: our dependency's API, treated as code.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

import porthole_pmb_api as api  # noqa: E402

# Real pmbootstrap 3.11.1 output, trimmed.
HELP = ("positional arguments:\n"
        "  {shutdown,index,kconfig,export,pkgrel_bump,status,init,log,zap,"
        "chroot,install,checksum,build,config,pull}\n")
CONFIG_ERR = ("pmbootstrap config: error: argument name: invalid choice: "
              "'channel' (choose from 'aports', 'device', 'ui', 'work')\n")


def test_subcommands_are_read_from_the_help_text():
    found = api.subcommands(HELP)
    assert "build" in found and "checksum" in found
    assert "lint" not in found


def test_config_keys_are_read_from_the_error_text():
    """argparse names every valid choice when it rejects one, which is a
    more reliable inventory than a version number: distro patches move the
    surface without moving the version."""
    found = api.config_keys(CONFIG_ERR)
    assert "device" in found and "ui" in found
    assert "channel" not in found


def test_a_missing_subcommand_is_detectable():
    assert not api.supports(api.subcommands(HELP), "lint")
    assert api.supports(api.subcommands(HELP), "build")


def test_everything_porthole_calls_exists_in_the_installed_pmbootstrap():
    """The guard. If pmbootstrap drops another subcommand or renames a config
    key, this fails here rather than surfacing as a false finding in front of
    a user."""
    have = api.probe()
    if not have["subcommands"]:
        return  # no pmbootstrap available; the floor job covers that
    missing = api.PORTHOLE_USES["subcommands"] - have["subcommands"]
    assert not missing, f"porthole calls pmbootstrap {sorted(missing)}, which this pmbootstrap does not have"
    gone = api.PORTHOLE_USES["config_keys"] - have["config_keys"]
    assert not gone, f"porthole reads pmbootstrap config {sorted(gone)}, which no longer exists"


def test_the_guard_can_actually_fail():
    """Positive control. Without a synthetic offender the assertion above can
    never fire and the test is decoration."""
    have = api.subcommands(HELP)
    assert api.PORTHOLE_USES["subcommands"] - have, \
        "the guard no longer detects a missing subcommand"


def test_an_unavailable_subcommand_is_blocked_not_a_finding():
    """`porthole aports lint` printed "lint found problems" for a subcommand
    pmbootstrap does not have. AGENTS.md section 6: a tool that broke must
    never be reported as an answer."""
    import porthole_cmd_aports as aports

    assert hasattr(aports, "_lint_unavailable"), \
        "cmd_lint has no unavailability path"
    message = aports._lint_unavailable("pmbootstrap 3.11.1 has no `lint` subcommand")
    assert "3.11.1" in message
    assert "found problems" not in message.lower()


def test_the_channel_is_read_from_pmaports_cfg():
    """Not from the branch. This checkout sits on `taimen-bringup` while
    pmaports.cfg declares `channel=edge`; deriving it from the branch reported
    the branch as the channel."""
    import porthole_cmd_channel as channel

    cfg_text = ("# Reference: https://postmarketos.org/pmaports.cfg\n"
                "[pmaports]\nversion=7\nchannel=edge\n")
    assert channel.channel_of_cfg(cfg_text) == "edge"


def test_a_pmaports_cfg_without_a_channel_says_so():
    import porthole_cmd_channel as channel

    assert channel.channel_of_cfg("[pmaports]\nversion=7\n") == ""


def test_switching_a_channel_is_blocked_not_reported_as_a_refusal():
    """The third instance of this branch's own bug. Reading the channel was
    fixed; WRITING it still shelled `pmbootstrap config channel <name>`, which
    3.11.1 rejects with argparse exit 2, and rendered that as "pmbootstrap
    refused the channel change" -- the identical sentence-shape to "lint found
    problems"."""
    import porthole_cmd_channel as channel
    from porthole_cli import Bail, EX_UNAVAILABLE

    original = api.missing
    api.missing = lambda kind, name: "pmbootstrap 3.11.1 has no `channel` config key"
    try:
        channel.require_channel_key(
            "v26.06", {"branch_pmaports": "v26.06"}, "/w/pmaports")
    except Bail as exc:
        assert exc.code == EX_UNAVAILABLE, exc.code
        assert "refused" not in exc.message.lower(), exc.message
        assert "branch" in exc.hint, exc.hint
    else:
        raise AssertionError("the channel write is not guarded by missing()")
    finally:
        api.missing = original


def test_a_pmbootstrap_that_still_has_the_channel_key_is_not_blocked():
    """Positive control: the guard must not refuse a pmbootstrap that works."""
    import porthole_cmd_channel as channel

    original = api.missing
    api.missing = lambda kind, name: ""
    try:
        channel.require_channel_key("v26.06", {}, "/w/pmaports")
    finally:
        api.missing = original


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
