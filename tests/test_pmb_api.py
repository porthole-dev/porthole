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
