#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole experiment`: the confound gate, and the probe format.

Motivated by docs/RETRO-2026-08-26.md item 10 -- three findings in one day were
later refuted by device state nobody had captured: a leftover aplay holding a
backend, a sound server holding a PCM, a wedged DSP. The measurement succeeded
every time; it was answering a question about a different device.

Nothing here needs a device.
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

import porthole_cmd_experiment as E  # noqa: E402

TMPXDG = tempfile.mkdtemp(prefix="porthole-exp-test-")


def run(*args, env=None):
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "PORTHOLE_ROOT": str(ROOT), "XDG_CONFIG_HOME": TMPXDG,
            "NO_COLOR": "1", "TK_DEVICE_STATE": "ABSENT"}
    base.update(env or {})
    p = subprocess.run([sys.executable, str(CLI), *args],
                       capture_output=True, text=True, env=base)
    return p.returncode, p.stdout, p.stderr


class FakeCtx:
    def __init__(self, root, device):
        self.root = str(root)
        self.cfg = {"PORTHOLE_DEVICE": device}


def test_probes_and_confounds_are_told_apart_by_the_bang():
    d = pathlib.Path(tempfile.mkdtemp(prefix="probes-"))
    (d / "profiles" / "dev").mkdir(parents=True)
    (d / "profiles" / "dev" / "probes.conf").write_text(
        "# a comment\n\nplain: echo one\n!dirty: echo two\n"
        "  spaced : echo three\nbroken-no-colon\nempty:\n")
    probes, confounds, path = E.load_probes(FakeCtx(d, "dev"))
    names = dict(probes)
    assert "plain" in names and names["plain"] == "echo one"
    assert "spaced" in names, "leading whitespace broke a probe"
    assert "dirty" in dict(confounds), "! did not mark a confound"
    assert "dirty" not in names, "a confound was also recorded as a probe"
    assert "broken-no-colon" not in names and "empty" not in names
    assert path is not None


def test_generic_probes_apply_with_no_profile_file():
    """A device with no probes.conf still gets boot_id, which is the one that
    catches a reboot mid-experiment -- results either side are incomparable."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="probes-none-"))
    probes, confounds, path = E.load_probes(FakeCtx(d, "nosuch"))
    assert path is None
    assert "boot_id" in dict(probes)
    assert confounds, "no confound checks at all without a profile"


def test_the_confound_check_cannot_clobber_the_callers_command():
    """It was a loop in the caller: `for name, command in confounds` rebound
    the user's argv, and subprocess.run tried to exec a probe string."""
    class Dev:
        def run(self, cmd, timeout=0):
            return "held" if "dirty" in cmd else ""
    command = ["true"]
    dirty = E._check_confounds(Dev(), [("x", "echo dirty"), ("y", "echo")])
    assert command == ["true"], "the caller's command was clobbered"
    assert dirty == [("x", "held")], dirty


def test_a_probe_that_raises_does_not_kill_the_run():
    class Dev:
        def run(self, cmd, timeout=0):
            raise OSError("gone")
    snap = E.snapshot(Dev(), [("a", "true")])
    assert "probe failed" in snap["a"]


def test_diff_reports_only_what_changed():
    changed = E._diff({"a": "1", "b": "x"}, {"a": "2", "b": "x"})
    assert changed == [("a", "1", "2")]


def test_running_nothing_is_a_usage_error():
    rc, out, err = run("-d", "google-taimen", "experiment")
    assert rc == 64, f"expected EX_USAGE, got {rc}: {out}{err}"


def test_probes_lists_without_touching_a_device():
    rc, out, err = run("-d", "google-taimen", "experiment", "probes", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    names = [p["name"] for p in payload["probes"]]
    assert "boot_id" in names
    assert payload["confounds"], "no confound checks listed"


def test_the_reference_profile_ships_probes():
    """The verb is worth little with generic probes alone -- what counts as
    contamination is a device fact."""
    conf = ROOT / "profiles" / "google-taimen" / "probes.conf"
    assert conf.is_file(), "the reference device has no probes.conf"
    text = conf.read_text()
    assert "!" in text, "no confound checks defined"
    # The self-matching grep trap: a bare `pgrep -a aplay` counts the ssh
    # command asking the question and is never empty.
    assert "[a]play" in text, "pgrep pattern would match its own command line"


# ------------------------------------------------- doctor: declared deps --

def test_depends_parsing_ignores_prose_and_comments():
    """`depends=` appears inside a prose comment in the reference APKBUILD, and
    an unanchored search parsed that sentence as a package list -- it returned
    ['(see', ',', 'a', 'not', 'separate', 'subpackage']."""
    import porthole_cmd_doctor as D
    d = pathlib.Path(tempfile.mkdtemp(prefix="apkb-"))
    ab = d / "pmaports" / "device" / "testing" / "device-x" / "APKBUILD"
    ab.parent.mkdir(parents=True)
    ab.write_text(
        "pkgname=device-x\n"
        "# this kernel depends= on a separate subpackage (see below), not here\n"
        'depends="\n'
        "\talsa-ucm-conf\n"
        "\t# a comment naming why, with words like pipewire in it\n"
        "\tpostmarketos-base-ui-audio-backend-pipewire\n"
        "\t$_somevar\n"
        '"\n'
        'subpackages="device-x-openrc"\n')
    deps, found = D._declared_depends(
        {"PORTHOLE_DEVICE_PKG": "device-x", "PORTHOLE_WORKDIR": str(d)}, d)
    assert deps == ["alsa-ucm-conf",
                    "postmarketos-base-ui-audio-backend-pipewire"], deps
    assert found == ab


def test_depends_parsing_degrades_without_pmaports():
    import porthole_cmd_doctor as D
    deps, found = D._declared_depends({"PORTHOLE_DEVICE_PKG": "device-x",
                                       "PORTHOLE_WORKDIR": "/nonexistent"},
                                      pathlib.Path("/nonexistent"))
    assert deps is None and found is None


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
