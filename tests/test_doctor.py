#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""doctor's install advice: the part that was wrong on the developer's own host.

`porthole doctor` printed `sudo dnf install android-tools` on Fedora
Silverblue, which reports ID=fedora and has no dnf at all. The table was
folklore -- asserted, never executed. tests/distro-matrix.sh runs the advice
against four real distros; these are the fast checks that do not need podman.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cmd_doctor as doctor  # noqa: E402


def test_an_atomic_family_falls_back_to_its_base():
    """Most tools install the same way on Silverblue as on Fedora; only the
    ones that genuinely differ carry their own entry, so the rest must fall
    through rather than landing on the generic `*`."""
    assert doctor.install_hint("ssh", "fedora-atomic") == \
        doctor.install_hint("ssh", "fedora")


def test_an_atomic_host_is_not_told_to_use_dnf():
    for tool in ("adb", "fastboot"):
        hint = doctor.install_hint(tool, "fedora-atomic")
        assert "dnf install" not in hint, (
            "an ostree host has no dnf, and this is the advice it was given "
            "on the machine porthole is developed on: " + hint)


def test_a_normal_fedora_still_gets_dnf():
    assert "dnf" in doctor.install_hint("adb", "fedora")


def test_pmbootstrap_is_not_installed_from_pypi():
    """PyPI's newest pmbootstrap is 2.1.0 -- the 3.x series is not published
    there at all -- so `pipx install pmbootstrap` silently installs a major
    version behind what this toolbox targets."""
    hint = doctor.install_hint("pmbootstrap", "fedora")
    assert "pipx install" not in hint and "pip install" not in hint, hint


def test_podman_has_a_hint_for_every_family_it_can_detect():
    """podman is the one remaining host prerequisite, so an unknown family
    still has to get something actionable."""
    for family in ("debian", "arch", "fedora", "fedora-atomic", "alpine",
                   "suse", "macos", "unknown"):
        assert doctor.install_hint("podman", family), family


def test_doctor_offers_fix_and_dry_run():
    flags = [names[0] for names, _kw in doctor.SPEC["args"]]
    assert "--fix" in flags and "--dry-run" in flags, flags


class _Ctx:
    def __init__(self, cfg):
        self.cfg = cfg


def test_a_dangling_pmb_sudo_fails_with_a_named_fix():
    """Reported from a real session: the broker was gone, PMB_SUDO still
    pointed at it, and the build died with exit 78 deep inside pmbootstrap
    without anything mentioning PMB_SUDO."""
    ch = doctor.Checks()
    doctor._check_pmb_sudo(ch, _Ctx({"PMB_SUDO": "/nonexistent/ph-sudo"}), {})
    row = ch.rows[-1]
    assert row["status"] == "fail", row
    assert "unset PMB_SUDO" in row["fix"], row["fix"]
    assert "78" in row["fix"], "the fix should name the exit code you would see"


def test_an_unset_pmb_sudo_is_fine():
    import os
    saved = os.environ.pop("PMB_SUDO", None)
    try:
        ch = doctor.Checks()
        doctor._check_pmb_sudo(ch, _Ctx({}), {})
        assert ch.rows[-1]["status"] == "ok", ch.rows[-1]
    finally:
        if saved is not None:
            os.environ["PMB_SUDO"] = saved


def test_any_pmb_sudo_at_all_is_caught():
    """The privilege broker is gone, so PMB_SUDO can only be a leftover -- but
    an export outlives the file it named, pmbootstrap invokes it directly, and
    a stale one kills a build with exit 78 naming nothing. Both agent reports
    that hit this had the variable set; neither could see why."""
    ch = doctor.Checks()
    doctor._check_pmb_sudo(ch, _Ctx({"PMB_SUDO": "/usr/local/libexec/porthole/ph-sudo"}), {})
    row = ch.rows[-1]
    assert row["status"] == "fail", row
    assert "unset PMB_SUDO" in row["fix"], row["fix"]

    saved = os.environ.pop("PMB_SUDO", None)
    try:
        ch = doctor.Checks()
        doctor._check_pmb_sudo(ch, _Ctx({}), {})
        assert ch.rows[-1]["status"] == "ok", ch.rows[-1]
    finally:
        if saved is not None:
            os.environ["PMB_SUDO"] = saved


def test_nothing_still_ships_or_names_the_privilege_broker():
    """Deleted, not deprecated. A fallback that still exists is one an agent
    can be talked into using, and this one granted a real sudoers entry while
    its own docs admitted it could not contain a determined chroot payload."""
    assert not (ROOT / "sandbox" / "ph-sudo").exists()
    assert not (ROOT / "sandbox" / "ph-sudo-client").exists()
    for name in ("AGENTS.md", "README.md", "docs/SANDBOX.md",
                 "skills/porthole-bringup/SKILL.md"):
        text = (ROOT / name).read_text()
        assert "sandbox install" not in text, f"{name} still offers the broker"


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
