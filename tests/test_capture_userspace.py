#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Capturing the device's locally-built package set before a wipe.

A from-scratch install reinstalls userspace from the local repo. On
2026-09-08 the device carried gst-plugins-good-1.28.5-r1 while the repo
held r50: the wipe would have silently swapped a hand-built package for a
different one. The manifest is what makes that a decision instead of an
accident.

Every test below builds its own fixture repo with `tempfile` and points
`PORTHOLE_LOCAL_REPO` at it. Proven necessary, not just tidy: with no
override, `repo_names()` falls back to `~/.local/var/porthole-sandbox`, which
exists only on a porter's own desk -- `HOME=/tmp/nonexistent-home-xyz
python3 tests/test_capture_userspace.py` failed 0/6 before this file used
fixtures, and CI's `ubuntu-latest` runners are exactly that: no such
directory, no such packages, an accidental pass reading real local state.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "ph-capture-userspace.sh"


def _repo_env(tmp, *apk_names, **env_overrides):
    """A local-repo directory holding exactly these built packages, named as
    apk filenames (`mesa-26.1.6-r14.apk`) -- the shape repo_names() reads --
    plus the environment that points the tool at it instead of any real
    ~/.local/var/porthole-sandbox.

    PORTHOLE_KERNEL_PKG is popped unconditionally first: a porter's own shell
    has it set (from profiles/<device>/device.env), and a test that wants the
    "unconfigured" fallback path must not silently pass just because it
    happens to run on that desk. `**env_overrides` reinstates whatever a
    given test actually wants."""
    repo = pathlib.Path(tmp) / "repo"
    repo.mkdir()
    for name in apk_names:
        (repo / name).touch()
    env = dict(os.environ, PORTHOLE_LOCAL_REPO=str(repo))
    env.pop("PORTHOLE_KERNEL_PKG", None)
    env.update(env_overrides)
    return env


def _parse(text, *apk_names, **env_overrides):
    """The tool's own parser, exercised through `--parse-only` against a
    fixture repo built just for this call."""
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run(["bash", str(TOOL), "--parse-only"], input=text,
                             capture_output=True, text=True, check=True,
                             env=_repo_env(tmp, *apk_names, **env_overrides))
    return out.stdout.split()


def _parse_raw(text, *apk_names, **env_overrides):
    """Full stdout, comment lines included -- `_parse`'s `.split()` would
    tear a `# excluded: ...` line into separate tokens."""
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run(["bash", str(TOOL), "--parse-only"], input=text,
                             capture_output=True, text=True, check=True,
                             env=_repo_env(tmp, *apk_names, **env_overrides))
    return out.stdout


def test_only_locally_built_packages_are_captured():
    """`apk info -v` lists everything. Only what the local repo built can be
    restored from it, and only that is at risk from a wipe."""
    text = ("mesa-26.1.6-r14\n"
            "musl-1.2.5-r9\n"
            "webkit2gtk-6.0-2.52.6-r63\n")
    got = _parse(text, "mesa-26.1.6-r14.apk", "webkit2gtk-6.0-2.52.6-r63.apk")
    assert "mesa-26.1.6-r14" in got
    assert "webkit2gtk-6.0-2.52.6-r63" in got
    assert "musl-1.2.5-r9" not in got, "an upstream package is not ours to restore"


def test_the_exact_pkgrel_is_kept_not_just_the_name():
    """gst-plugins-good on the device was r1 and in the repo r50. A manifest
    that recorded only the name would restore the wrong build and report
    success."""
    got = _parse("gst-plugins-good-1.28.5-r1\n", "gst-plugins-good-1.28.5-r50.apk")
    assert got == ["gst-plugins-good-1.28.5-r1"]


# ---------------------------------------------------- device/firmware --
#
# Confirmed live on this hardware, not theoretical: the sandbox repo builds
# device-google-taimen, device-google-taimen-kernel-mainline,
# device-google-taimen-openrc and firmware-google-taimen, and the device has
# them installed. Restoring them with `apk add` right after a from-scratch
# install would run at Gate C, on a freshly flashed phone --
# brain/traps/installing-firmware-can-flash-the-boot-partition.md and
# brain/traps/a-sideloaded-device-apk-can-eat-the-radio-stack.md say what
# happens next. They stay in the manifest, as evidence of what was on the
# device, but as a `# excluded:` comment `restore`'s own `grep -v '^#'`
# already skips -- one mechanism, not a second filter to keep in sync.

def test_a_firmware_package_is_excluded_from_the_restorable_set():
    raw = _parse_raw("firmware-google-taimen-20250505-r4\n",
                      "firmware-google-taimen-20250505-r4.apk")
    # A restorable line is the bare name-version, with no "#" -- checking
    # `.split()` tokens would find the name INSIDE the excluded comment too.
    assert "firmware-google-taimen-20250505-r4" not in raw.splitlines(), \
        "a firmware package must never reach restore's apk add"
    assert "# excluded: firmware-google-taimen-20250505-r4" in raw.splitlines()


def test_a_device_package_is_excluded_the_same_way():
    raw = _parse_raw("device-google-taimen-1-r40\n",
                      "device-google-taimen-1-r40.apk")
    assert "device-google-taimen-1-r40" not in raw.splitlines()
    assert "# excluded: device-google-taimen-1-r40" in raw.splitlines()


def test_device_mapper_not_built_here_is_neither_restored_nor_excluded():
    """device-mapper is upstream -- nothing in the fixture repo builds it.
    It must vanish the same way musl does, not get filed under "excluded"."""
    raw = _parse_raw("device-mapper-2.03.35-r6\n")  # repo builds nothing
    assert "excluded" not in raw
    assert raw.split() == [], "not locally built, so not restorable either"


def test_a_locally_built_device_mapper_would_be_excluded_too():
    """The controller's ruling: leave the bare `device-*`/`firmware-*` prefix
    match as is rather than tighten it to "packages this device port owns".
    This is the documented cost of that choice (see parse()'s comment) -- if
    the repo ever built something literally named device-mapper, it would be
    excluded by the same match as device-google-taimen. Asymmetric on
    purpose: wrongly excluding costs a rebuild, wrongly restoring a
    device/firmware package can flash boot or kill the radio."""
    raw = _parse_raw("device-mapper-2.03.35-r6\n", "device-mapper-2.03.35-r6.apk")
    assert "# excluded: device-mapper-2.03.35-r6" in raw.splitlines()


def test_a_plain_package_is_still_captured_unmarked():
    got = _parse("mesa-26.1.6-r14\n", "mesa-26.1.6-r14.apk")
    assert got == ["mesa-26.1.6-r14"]


# --------------------------------------------------------------- kernel --
#
# Confirmed live on this hardware: the real device carried
# linux-postmarketos-qcom-msm8998-7.2-7.2.2-r21, which the device-*/firmware-*
# match alone let straight through -- it starts with neither. Two hazards,
# not one: taimen's deviceinfo sets deviceinfo_flash_kernel_on_update="true",
# so `apk add` on a kernel package flashes boot, same hazard class as
# firmware; and the chroot/local repo already carry r31, newer than the
# device's r21, so restoring would silently downgrade it. "Locally built"
# does not imply "safe to reinstall" -- the install owns the kernel too.

KERNEL_PKG = "linux-postmarketos-qcom-msm8998-7.2"


def test_the_configured_kernel_package_is_excluded():
    raw = _parse_raw("%s-7.2.2-r21\n" % KERNEL_PKG,
                      "%s-7.2.2-r31.apk" % KERNEL_PKG,
                      PORTHOLE_KERNEL_PKG=KERNEL_PKG)
    assert "%s-7.2.2-r21" % KERNEL_PKG not in raw.splitlines(), \
        "the kernel must never reach restore's apk add"
    assert "# excluded: %s-7.2.2-r21" % KERNEL_PKG in raw.splitlines()


def test_a_kernel_subpackage_is_excluded_too():
    sub = KERNEL_PKG + "-dbg"
    raw = _parse_raw("%s-7.2.2-r21\n" % sub, "%s-7.2.2-r31.apk" % sub,
                      PORTHOLE_KERNEL_PKG=KERNEL_PKG)
    assert "# excluded: %s-7.2.2-r21" % sub in raw.splitlines()


def test_an_unrelated_linux_prefixed_package_not_built_here_is_untouched():
    """linux-firmware-ath10k is upstream, not this repo's kernel aport -- the
    fixture repo builds nothing under that name, so it must vanish like musl
    does (not restorable, not "excluded"), regardless of PORTHOLE_KERNEL_PKG.
    Proves the linux-* fallback heuristic cannot reach a package the repo
    never built in the first place -- repo_names() gates before it does."""
    raw = _parse_raw("linux-firmware-ath10k-20250101-r1\n")  # repo builds nothing
    assert "excluded" not in raw
    assert raw.split() == []


def test_the_unconfigured_fallback_excludes_a_repo_built_linux_package():
    """Different branch from the test above, on purpose: that one proves
    repo_names() stops an UNBUILT linux-* package before is_kernel_pkg() ever
    runs -- it never exercises the fallback's own `linux-*` match. Here the
    fixture repo DOES build the package, so the backstop branch itself has to
    fire. This is what actually runs in production for any device profile
    whose device.env does not set PORTHOLE_KERNEL_PKG.

    Popping PORTHOLE_KERNEL_PKG (`_repo_env`'s default) is not enough on its
    own here: this desk also has PORTHOLE_DEVICE=google-taimen exported
    directly, and even without that, ph-lib.sh reads
    ~/.config/porthole/config.env, which sets PORTHOLE_DEVICE, and a known
    PORTHOLE_DEVICE loads profiles/google-taimen/device.env right back --
    which sets PORTHOLE_KERNEL_PKG again. Both paths have to be blocked to
    actually reach the unconfigured fallback."""
    with tempfile.TemporaryDirectory() as tmp:
        env = _repo_env(tmp, "linux-lts-6.6.10-r3.apk")
        env.pop("PORTHOLE_DEVICE", None)
        env["HOME"] = str(pathlib.Path(tmp) / "no-such-home")
        out = subprocess.run(["bash", str(TOOL), "--parse-only"],
                             input="linux-lts-6.6.10-r3\n", capture_output=True,
                             text=True, check=True, env=env)
    assert "# excluded: linux-lts-6.6.10-r3" in out.stdout.splitlines()


# --------------------------------------------------- capture must not lie --
#
# parse() always returns 0 -- it exists to filter a stream, not to report on
# whatever fed it. Without `set -o pipefail`, `tk_run "apk info -v" | parse`
# in the `capture` case would inherit that 0 even when tk_run's ssh call
# failed outright, and `capture` would write a clean, dated, "captured 0
# package(s)" manifest and exit 0 -- indistinguishable from a real capture of
# a device with nothing locally built. At Gate C that manifest sails through
# and the wipe proceeds with the hand-built userspace gone for good.

def _fake_ssh_path(tmp, script):
    """A directory holding a fake `ssh`, meant to sit ahead of the real one
    on PATH so tk_run's `ssh ...` calls reach it instead of a real device."""
    bindir = pathlib.Path(tmp) / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(script)
    ssh.chmod(0o755)
    return str(bindir)


def test_an_unreachable_device_does_not_produce_a_success_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        env = _repo_env(tmp, "mesa-26.1.6-r14.apk")
        env["PATH"] = _fake_ssh_path(tmp, "#!/bin/sh\nexit 255\n") + ":" + env["PATH"]
        outfile = pathlib.Path(tmp) / "manifest"
        proc = subprocess.run(["bash", str(TOOL), "capture", str(outfile)],
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 69, (proc.returncode, proc.stderr)
        assert not outfile.exists(), \
            "a failed capture must not write a manifest at all, not even an empty one"


def test_a_device_with_nothing_locally_built_installed_is_a_hard_error():
    """ssh works, `apk info -v` answers, but not one installed package
    matches anything the local repo builds. Distinct from the unreachable
    case (69, "the check did not happen") -- here the check ran and found
    genuinely nothing, which is exit 1."""
    with tempfile.TemporaryDirectory() as tmp:
        env = _repo_env(tmp, "mesa-26.1.6-r14.apk")
        env["PATH"] = _fake_ssh_path(
            tmp, "#!/bin/sh\necho musl-1.2.5-r9\n") + ":" + env["PATH"]
        outfile = pathlib.Path(tmp) / "manifest"
        proc = subprocess.run(["bash", str(TOOL), "capture", str(outfile)],
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 1, (proc.returncode, proc.stderr)
        assert not outfile.exists()


# ------------------------------------------------------------ relative path --
#
# The tool `cd`s to tools/ (to source ./ph-lib.sh by a relative path) before
# it ever looks at OUTFILE/INFILE. Reproduced: `porthole run
# tools/ph-capture-userspace.sh capture .run/foo.manifest` from the repo root
# failed with "No such file or directory" because ".run/foo.manifest" was
# then being opened against tools/.run/foo.manifest instead.

def test_a_relative_outfile_path_resolves_against_the_callers_cwd_not_tools():
    # A stray write into the real tools/ (this checkout is shared) is exactly
    # the bug under test, so clean it up even if an assertion below fails.
    stray = TOOL.parent / "out.manifest"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = pathlib.Path(tmp) / "somewhere-else"
            cwd.mkdir()
            env = _repo_env(tmp, "mesa-26.1.6-r14.apk")
            env["PATH"] = _fake_ssh_path(
                tmp, "#!/bin/sh\necho mesa-26.1.6-r14\n") + ":" + env["PATH"]
            proc = subprocess.run(["bash", str(TOOL), "capture", "out.manifest"],
                                  capture_output=True, text=True, env=env, cwd=cwd)
            assert proc.returncode == 0, proc.stderr
            assert (cwd / "out.manifest").exists(), \
                "the relative OUTFILE must land in the caller's cwd, not tools/"
            assert not stray.exists(), \
                "must not have been written against tools/ instead"
    finally:
        stray.unlink(missing_ok=True)


def test_a_relative_infile_path_resolves_against_the_callers_cwd_not_tools():
    """Mirrors the outfile test above for `restore`. No repo fixture needed --
    restore never calls repo_names(), it just reads the manifest and builds
    apk specs -- so a fake `ssh` that only proves it was reached is enough to
    keep this hermetic. Reverting the fix makes this look for
    tools/in.manifest and exit 64 "usage" instead."""
    with tempfile.TemporaryDirectory() as tmp:
        cwd = pathlib.Path(tmp) / "somewhere-else"
        cwd.mkdir()
        (cwd / "in.manifest").write_text("mesa-26.1.6-r14\n")
        env = dict(os.environ)
        env["PATH"] = _fake_ssh_path(
            tmp, "#!/bin/sh\necho apk-add-reached >&2\n") + ":" + env["PATH"]
        proc = subprocess.run(["bash", str(TOOL), "restore", "in.manifest"],
                              capture_output=True, text=True, env=env, cwd=cwd)
        assert proc.returncode == 0, proc.stderr
        assert "apk-add-reached" in proc.stderr, \
            "restore never reached the device call -- it did not find in.manifest"


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
