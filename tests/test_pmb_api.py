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

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

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
    return _runner.run(globals())


def test_the_guard_names_every_subcommand_porthole_invokes():
    """This module exists because pmbootstrap 3.11.1 removed `lint` and
    dropped the `channel` config key, and porthole rendered both
    disappearances as ANSWERS rather than as breakage -- the cardinal sin in
    AGENTS.md section 6.

    It then guarded 7 of the 18 subcommands porthole actually calls. `export`
    was not among them, and `porthole build image` ends in `pmbootstrap
    export`: a rename upstream would leave this suite green and break the
    build in the field.

    Scanned rather than listed, so the guard cannot narrow again while this
    test keeps passing."""
    invoked = api.invoked_subcommands(ROOT)
    guarded = api.PORTHOLE_USES["subcommands"]
    # Guarded calls are the UNCONDITIONAL ones. A call behind a `missing()`
    # check is deliberately absent -- listing it here would fail the guard on
    # every pmbootstrap that dropped it, which is the point of guarding it.
    unguarded = invoked - guarded - api.GUARDED_AT_CALL_SITE
    assert not unguarded, (
        "porthole invokes these and nothing checks they still exist:\n  "
        + "\n  ".join(sorted(unguarded)))

    # THE ASSERTION ABOVE CANNOT DETECT A SCAN THAT FINDS TOO LITTLE. Losing a
    # source only shrinks `invoked`, which makes `unguarded` empty -- so a
    # broken pattern reads as a clean bill of health. Both sources therefore
    # need naming, or the scan quietly stops scanning and this test applauds:
    #
    #   the shell scan of tools/*.sh is the ONLY source for these four, and
    #   `export` is the one that matters -- `porthole build image` ends in
    #   `pmbootstrap export`, which is what this whole task is about.
    assert {"export", "flasher", "index", "install"} <= invoked, sorted(invoked)
    #   porthole_cmd_aports.pmb() builds pmbootstrap's argv itself, so the
    #   subcommand never appears beside the word and the argv pattern misses
    #   it. All three are guarded already, so the omission would have surfaced
    #   only as the NEXT one being missed.
    assert {"checksum", "lint", "pkgrel_bump"} <= invoked, sorted(invoked)
    # Finding too MUCH needs no assertion of its own: this repo's comments and
    # hints mention `pmbootstrap status`, `log`, `pull` and `zap` constantly,
    # and a regex loose enough to sweep prose puts all four in `unguarded` and
    # fails above. Verified by loosening it on purpose.


if __name__ == "__main__":
    sys.exit(main())
