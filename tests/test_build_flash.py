#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""build and flash: the refusals, which are the whole point.

Flashing is the most irreversible thing this toolkit does and it had no verb
at all -- the loop lived in a shell file that had to be SOURCED, so
`porthole run` could not reach it, and taimen's slot and dtbo were written into
a file whose header claims `scope: generic` while PORTHOLE_SLOT_FORBIDDEN and
PORTHOLE_ACTIVE_SLOT sat unread in the schema.

Every test here is a refusal. Nothing in this file builds or flashes anything.
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

TMPXDG = tempfile.mkdtemp(prefix="porthole-build-test-")


def run(*args, env=None):
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "PORTHOLE_ROOT": str(ROOT), "XDG_CONFIG_HOME": TMPXDG,
            "NO_COLOR": "1", "TK_DEVICE_STATE": "ABSENT"}
    base.update(env or {})
    p = subprocess.run([sys.executable, str(CLI), *args],
                       capture_output=True, text=True, env=base)
    return p.returncode, p.stdout, p.stderr


DEV = "google-taimen"


# ----------------------------------------------------------------- refusals --

def test_flash_refuses_a_forbidden_slot():
    """Recovery from the bootloader cannot re-arm a slot, so this is the last
    point at which the mistake is still cheap."""
    rc, out, err = run("-d", DEV, "flash", "--slot", "a", "--force", "--yes")
    assert rc != 0, "it agreed to arm a forbidden slot"
    assert "SLOT_FORBIDDEN" in (out + err)


def test_flash_refuses_unless_the_device_is_in_fastboot():
    rc, out, err = run("-d", DEV, "flash", "--yes")
    assert rc != 0
    assert "FASTBOOT" in (out + err)


def test_flash_does_nothing_without_yes():
    rc, out, err = run("-d", DEV, "flash", "--force")
    assert rc == 0, err
    assert "would flash" in out
    assert "flashed" not in out


def test_flash_shows_the_slot_policy_rather_than_advising_about_it():
    """"Check the slot policy before you agree" is advice. Showing the policy
    is a mechanism."""
    rc, out, _ = run("-d", DEV, "flash", "--force")
    for field in ("set active", "forbidden", "dtbo", "slots"):
        assert field in out, f"the preview does not show {field!r}"


def test_flash_warns_when_the_slot_layout_was_never_probed():
    """HAS_AB_SLOTS="0" is both the shipped default and a real answer."""
    rc, out, err = run("-d", DEV, "flash", "--force")
    assert "never probed" in (out + err) or "SLOTS_PROBED" in (out + err)


def test_build_does_nothing_without_yes():
    # The per-device workdir lives in the user config, which this harness
    # deliberately does not have -- so supply it the way a one-off would.
    rc, out, err = run("-d", DEV, "build",
                       env={"PORTHOLE_WORKDIR": str(ROOT)})
    assert rc == 0, err + out
    assert "would build" in out
    assert "$ source" not in out, "it started a build on a preview"


def test_build_reports_every_missing_value_at_once():
    """An envkernel build is minutes long. Finding out about the second
    missing key after fixing the first is how an afternoon goes."""
    rc, out, err = run("-d", "google-cheetah", "build", "--yes")
    assert rc != 0
    assert "cannot build yet" in (out + err)


# -------------------------------------------------------- no taimen values --

def test_the_build_script_carries_no_device_specific_values():
    """Its header says `scope: generic` and it hardcoded msm8998 dtsi names,
    one developer's envkernel path, taimen's dtbo and taimen's slot."""
    text = (ROOT / "tools" / "ph-build.sh").read_text()
    code = "\n".join(l for l in text.splitlines()
                     if not l.lstrip().startswith("#"))
    for needle in ("dtbo_idx12", "set_active b", "msm8998-google-wahoo",
                   "pmi8998.dtsi", "$HOME/src/pmbootstrap"):
        assert needle not in code, (
            f"ph-build.sh still hardcodes {needle!r} while claiming to be "
            f"generic")


def test_the_build_script_reads_the_slot_policy():
    text = (ROOT / "tools" / "ph-build.sh").read_text()
    for key in ("PORTHOLE_SLOT_FORBIDDEN", "PORTHOLE_ACTIVE_SLOT",
                "PORTHOLE_DTBO_IMG", "PORTHOLE_HAS_AB_SLOTS"):
        assert key in text, f"ph-build.sh ignores {key}"


def test_envkernel_is_discovered_rather_than_assumed():
    text = (ROOT / "tools" / "ph-build.sh").read_text()
    assert "_ph_find_envkernel" in text
    assert "PORTHOLE_ENVKERNEL" in text, "no way to point it at a checkout"


def test_the_build_script_is_still_valid_bash():
    rc = subprocess.run(["bash", "-n", str(ROOT / "tools" / "ph-build.sh")],
                        capture_output=True).returncode
    assert rc == 0, "ph-build.sh does not parse"


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
