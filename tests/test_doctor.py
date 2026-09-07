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
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cmd_doctor as doctor  # noqa: E402

CLI = ROOT / "bin" / "porthole"
TMPXDG = tempfile.mkdtemp(prefix="porthole-doctor-test-")


def run(*args, env=None, stdin=""):
    """Invoke the CLI in a clean environment. env -i semantics matter: the
    developer running the tests usually has PHONE exported."""
    base = {
        "PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
        "PORTHOLE_ROOT": str(ROOT), "XDG_CONFIG_HOME": TMPXDG,
        "NO_COLOR": "1",
    }
    base.update(env or {})
    proc = subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, env=base, input=stdin)
    return proc.returncode, proc.stdout, proc.stderr


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


def _row(ch, name):
    """The row a check emitted, BY NAME.

    `ch.rows[-1]` was the idiom here and it broke the moment _check_pmb_sudo
    emitted a second row -- three tests failed while asserting nothing about
    the thing they were testing. A check is allowed to grow rows; a test that
    reads them positionally is not testing what it says it is.
    """
    hits = [r for r in ch.rows if r["name"] == name]
    assert hits, f"no row named {name!r} in {[r['name'] for r in ch.rows]}"
    return hits[-1]


def test_a_dangling_pmb_sudo_fails_with_a_named_fix():
    """Reported from a real session: the broker was gone, PMB_SUDO still
    pointed at it, and the build died with exit 78 deep inside pmbootstrap
    without anything mentioning PMB_SUDO."""
    ch = doctor.Checks()
    doctor._check_pmb_sudo(ch, _Ctx({"PMB_SUDO": "/nonexistent/ph-sudo"}), {})
    row = _row(ch, "host: PMB_SUDO")
    assert row["status"] == "fail", row
    assert "unset PMB_SUDO" in row["fix"], row["fix"]
    assert "78" in row["fix"], "the fix should name the exit code you would see"


def test_an_unset_pmb_sudo_is_fine():
    import os
    saved = os.environ.pop("PMB_SUDO", None)
    try:
        ch = doctor.Checks()
        doctor._check_pmb_sudo(ch, _Ctx({}), {})
        assert _row(ch, "host: PMB_SUDO")["status"] == "ok", ch.rows
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
    row = _row(ch, "host: PMB_SUDO")
    assert row["status"] == "fail", row
    assert "unset PMB_SUDO" in row["fix"], row["fix"]

    saved = os.environ.pop("PMB_SUDO", None)
    try:
        ch = doctor.Checks()
        doctor._check_pmb_sudo(ch, _Ctx({}), {})
        assert _row(ch, "host: PMB_SUDO")["status"] == "ok", ch.rows
    finally:
        if saved is not None:
            os.environ["PMB_SUDO"] = saved


def test_a_leftover_broker_binary_is_reported_even_with_pmb_sudo_unset():
    """Deleting the installer uninstalled nothing.

    `sandbox/ph-sudo` and the install/audit/uninstall verbs went on
    2026-08-29; a host provisioned before then still carries the helper and
    the sudoers entry that makes it work, which is standing host privilege --
    the one property the workspace exists to remove. Nothing looked for it
    until an agent tripped over the export three separate times.
    """
    ch = doctor.Checks()
    doctor._check_leftover_broker(ch, "")
    row = _row(ch, "host: ph-sudo broker")
    if any(os.path.exists(p) for p in doctor.BROKER_PATHS):
        assert row["status"] == "warn", row
        assert "sudoers" in row["fix"], row["fix"]
    else:
        assert row["status"] == "ok", row

    # The positive control: a path that IS there must warn, whatever this
    # host happens to have installed.
    ch = doctor.Checks()
    doctor._check_leftover_broker(ch, __file__)
    row = _row(ch, "host: ph-sudo broker")
    assert row["status"] == "warn", row
    assert __file__ in row["fix"], row["fix"]

    # ...and one that is not there must not.
    ch = doctor.Checks()
    doctor._check_leftover_broker(ch, "/nonexistent/ph-sudo")
    row = _row(ch, "host: ph-sudo broker")
    expected = "warn" if any(os.path.exists(p)
                             for p in doctor.BROKER_PATHS) else "ok"
    assert row["status"] == expected, row


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


def test_an_absent_device_is_probed_once_not_twice():
    """Two independent checks each opened their own connection to the same
    phone, and neither knew the other had just failed. On an unplugged device
    that is 2 s for the state row plus 5 s for the key row, for one answer.

    PORTHOLE_DEVICE_STATE is how the repo already short-circuits the state
    probe in tests; the point of this test is that the KEY probe honours it
    too."""
    rc, out, err = run("doctor", env={"PORTHOLE_DEVICE_STATE": "absent"})
    assert "device: state" in out, out
    assert "device key" in out, out
    key = [l for l in out.splitlines() if "device key" in l]
    assert key and "could not be asked" in key[0], key


def main():
    return _runner.run(globals())



def test_a_profile_that_disagrees_with_itself_about_the_kernel_is_caught():
    """The exact 2026-08-29 state: the 6.18 -> 7.2 move updated the two keys a
    build reads and left PORTHOLE_KERNEL_BRANCH naming the old branch. Nothing
    reads that key, so no build could fail on it and drift() -- which compares
    the environment against the profile -- saw every layer agreeing."""
    ch = doctor.Checks()
    doctor.check_kernel_series(ch, {
        "PORTHOLE_DEVICE": "google-taimen",
        "PORTHOLE_KERNEL_PKG": "linux-postmarketos-qcom-msm8998-7.2",
        "PORTHOLE_KCONFIG_FILE": "config-postmarketos-qcom-msm8998-7.2.aarch64",
        "PORTHOLE_KERNEL_BRANCH": "taimen-v6.18-wip",
    })
    row = ch.rows[-1]
    assert row["status"] == "warn", row
    assert "PORTHOLE_KERNEL_BRANCH=6.18" in row["detail"], row["detail"]
    assert "PORTHOLE_KERNEL_PKG=7.2" in row["detail"], row["detail"]
    assert "google-taimen" in row["doc"], row["doc"]
    assert row["name"] == "config: kernel series", row


def test_a_profile_where_every_key_names_the_same_series_is_quiet():
    ch = doctor.Checks()
    doctor.check_kernel_series(ch, {
        "PORTHOLE_KERNEL_PKG": "linux-postmarketos-qcom-msm8998-7.2",
        "PORTHOLE_KCONFIG_FILE": "config-postmarketos-qcom-msm8998-7.2.aarch64",
        "PORTHOLE_KERNEL_BRANCH": "taimen-v7.2",
    })
    assert ch.rows[-1]["status"] == "ok", ch.rows[-1]


def test_a_soc_number_is_not_read_as_a_kernel_version():
    """cheetah's aport is `linux-postmarketos-gs201` and its other two keys are
    blank. A check that matched any digits would invent a disagreement between
    gs201 and nothing, on the one profile that has nothing to disagree about.
    msm8998 has no dot either, which is what keeps taimen's own aport from
    matching twice."""
    ch = doctor.Checks()
    doctor.check_kernel_series(ch, {
        "PORTHOLE_KERNEL_PKG": "linux-postmarketos-gs201",
        "PORTHOLE_KCONFIG_FILE": "",
        "PORTHOLE_KERNEL_BRANCH": "",
    })
    assert ch.rows == [], ch.rows


def test_a_non_executable_fastboot_is_not_reported_ok():
    """_resolve's isfile fallback accepted any file that EXISTS. FASTBOOT is a
    required row, so a config naming a non-executable file rendered green on
    the one check whose whole purpose is to hard-fail."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "fastboot"
        fake.write_text("not executable\n")
        os.chmod(fake, 0o644)
        assert doctor._resolve({"FASTBOOT": str(fake)}, "FASTBOOT",
                               "fastboot") is None


def test_an_executable_fastboot_still_resolves():
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "fastboot"
        fake.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(fake, 0o755)
        assert doctor._resolve({"FASTBOOT": str(fake)}, "FASTBOOT",
                               "fastboot") == str(fake)


def test_a_broken_shebang_does_not_pass_as_ok():
    """A +x script whose interpreter is gone passes shutil.which and dies at
    exec with 126. That is the ordinary pipx failure -- a venv whose base
    python was removed -- and doctor called it ok for as long as it existed."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "pmbootstrap"
        fake.write_text("#!/nonexistent/python\nprint(1)\n")
        os.chmod(fake, 0o755)
        why = doctor._runs(str(fake))
        assert why, "a broken shebang must not read as ok"
        assert "bad interpreter" in why and "nonexistent" in why, why


def test_a_working_tool_reports_no_reason():
    """A hermetic stand-in rather than a real tool: /bin/sh is dash on some
    hosts and dash has no --version, so probing it would fail this test on a
    perfectly good box. The contract under test is "exit 0 means usable"."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "worksfine"
        fake.write_text("#!/bin/sh\necho 'worksfine 1.0'\n")
        os.chmod(fake, 0o755)
        assert doctor._runs(str(fake)) == ""


def test_a_tool_that_answers_the_wrong_flag_is_a_reason():
    """`ssh --version` exits 255 with a usage block. Asking a tool the wrong
    question must read as broken, which is why the flag is per-tool."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "picky"
        fake.write_text("#!/bin/sh\necho 'unknown option' >&2\nexit 255\n")
        os.chmod(fake, 0o755)
        why = doctor._runs(str(fake))
        assert "255" in why and "unknown option" in why, why


def test_an_env_form_shebang_with_a_dead_target_is_caught():
    """The case reported from a NixOS host in PR #2: a pip-generated
    `#!/usr/bin/env <python>` outliving the python it names. env EXISTS, so
    exec succeeds and env itself fails with 127 -- a different path from a
    direct shebang, which fails at exec with ENOENT. Both must be caught."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "pmbootstrap"
        fake.write_text("#!/usr/bin/env /no/such/python3\nprint(1)\n")
        os.chmod(fake, 0o755)
        why = doctor._runs(str(fake))
        assert why, "an env-form dead interpreter must not read as ok"
        assert "/no/such/python3" in why, why


def test_ssh_is_probed_with_the_flag_ssh_actually_takes():
    """`ssh --version` is not a thing -- it exits 255 with a usage block. A
    uniform --version probe reported a working ssh as FAIL on the very first
    host this ran on, which is the same false-confidence bug in reverse."""
    ssh = shutil.which("ssh")
    if ssh:
        assert doctor._runs(ssh, "-V") == ""
    flags = {t: f for t, _k, _r, _w, f in doctor.HOST_TOOLS}
    assert flags["ssh"] == "-V", flags


def test_a_tool_that_is_absent_is_a_reason_not_a_crash():
    """_runs is handed a resolved path, but a tool can vanish between the
    resolve and the exec. That must be a row, not a traceback."""
    assert doctor._runs("/nonexistent/tool")


def test_check_host_fails_a_required_row_whose_tool_cannot_run():
    """The whole point: `ok host: fastboot /usr/bin/fastboot` for something
    that cannot start is worse than no check, because it converts "the tools
    do nothing" into "the tools do nothing and doctor says they are fine"."""
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("ssh", "fastboot", "adb", "pmbootstrap", "shellcheck"):
            fake = pathlib.Path(tmp) / name
            fake.write_text("#!/nonexistent/python\n")
            os.chmod(fake, 0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = tmp
        try:
            ch = doctor.Checks()
            doctor.check_host(ch, {}, "fedora")
        finally:
            os.environ["PATH"] = old
        rows = {r["name"]: r for r in ch.rows}
        assert rows["host: fastboot"]["status"] == "fail", rows["host: fastboot"]
        assert rows["host: ssh"]["status"] == "fail", rows["host: ssh"]
        # Optional tools warn rather than fail: pmbootstrap is not needed to
        # probe a device, and a broken one must not stop doctor reporting.
        assert rows["host: pmbootstrap"]["status"] == "warn", rows["host: pmbootstrap"]
        assert rows["host: fastboot"]["fix"], "a failing row must name a fix"


# ------------------------------------------------------- the device key ---
# doctor printed a green device key for a key the phone had never been told
# about, because it checked that the FILE exists. So doctor was ok while every
# workspace push failed with `scp: Connection closed`.

def test_the_device_key_row_reports_three_states():
    """Three, not two. An unreachable device is not evidence the key is bad,
    and a check that cries failure when it does not know is one people learn
    to scroll past."""
    seen = {}
    for value in (True, False, None):
        ch = doctor.Checks()
        doctor._device_key_row(ch, {"device_key": "/k",
                                    "device_key_authorized": value})
        seen[value] = ch.rows[-1]["status"]
    assert seen == {True: "ok", False: "fail", None: "warn"}, seen


def test_a_missing_key_is_not_reported_as_refused():
    ch = doctor.Checks()
    doctor._device_key_row(ch, {"device_key": "", "device_key_authorized": None})
    assert ch.rows[-1]["status"] == "warn", ch.rows[-1]
    assert "not created" in ch.rows[-1]["detail"], ch.rows[-1]


def test_a_refused_key_names_the_command_that_fixes_it():
    ch = doctor.Checks()
    doctor._device_key_row(ch, {"device_key": "/k", "device_key_authorized": False})
    fix = ch.rows[-1]["fix"]
    assert "ssh-keygen -y" in fix and "authorized_keys" in fix, fix


def test_doctor_prints_the_key_fix_and_does_not_perform_it():
    """Installing a key is a privileged write to the device. doctor names
    fixes it will not run, and this is not the place to make an exception."""
    src = (ROOT / "lib" / "porthole_cmd_doctor.py").read_text()
    body = src.split("def _device_key_row")[1].split("\ndef ")[0]
    for verb in ("subprocess.run", "os.system", "tee "):
        assert verb not in body, verb


def test_an_agent_key_cannot_mask_an_uninstalled_device_key():
    """Without IdentitiesOnly a working key in the user's agent answers for the
    device key, and the check passes for the wrong reason."""
    import porthole_cmd_sandbox as sandbox
    src = (ROOT / "lib" / "porthole_cmd_sandbox.py").read_text()
    body = src.split("def _device_key_authorized")[1].split("\ndef ")[0]
    assert "IdentitiesOnly" in body and "BatchMode" in body


def test_only_a_refusal_counts_as_unauthorized():
    """A device that is off, or a name that does not resolve, is unknown."""
    import porthole_cmd_sandbox as sandbox
    with tempfile.TemporaryDirectory() as tmp:
        key = pathlib.Path(tmp) / "k"
        key.write_text("x")
        # No PHONE configured at all: nothing to ask, so nothing is claimed.
        assert sandbox._device_key_authorized({}, key) is None
        # A host that cannot resolve is unknown, never False.
        got = sandbox._device_key_authorized(
            {"PHONE": "porthole-nonexistent.invalid"}, key)
        assert got is None, got


def test_unset_pmos_password_is_a_warning_not_a_failure():
    """It gates only `kernel` and `upgrade`, so an absent value is not broken.

    tkbuild hard-requires it and dies with a bare shell parameter error.
    doctor is where that becomes findable -- but a host doing `mod` work all
    day is fine without it, and a FAIL there would train people to ignore the
    row.
    """
    import porthole_cmd_doctor as doctor

    ch = doctor.Checks()
    doctor._check_pmos_password(ch, {})
    # Checks.rows holds dicts, not objects -- see lib/porthole_cmd_doctor.py.
    row, = [c for c in ch.rows if "PMOS_PASSWORD" in c["name"]]
    assert row["status"] == "warn", row["status"]
    assert "kernel" in row["fix"] or "kernel" in row["detail"], row


def test_a_set_pmos_password_is_ok_and_never_printed():
    import porthole_cmd_doctor as doctor

    ch = doctor.Checks()
    doctor._check_pmos_password(ch, {"TK_PMOS_PASSWORD": "hunter2"})
    row, = [c for c in ch.rows if "PMOS_PASSWORD" in c["name"]]
    assert row["status"] == "ok", row["status"]
    blob = " ".join([row["name"], row["detail"], row["fix"]])
    assert "hunter2" not in blob, "doctor must never echo the password"


def test_a_build_does_not_inherit_pmb_sudo():
    """doctor already calls it a leftover; a build should not carry it.

    A stale PMB_SUDO export kills a build with exit 78 deep inside
    pmbootstrap, with nothing anywhere saying the words PMB_SUDO.

    The scrub moved to porthole_cli.child_env (#63) so that `pkg` gets it too;
    this assertion stays HERE because it is the doctor story's other half --
    doctor is what tells you the variable is set, and this is what makes being
    told survivable.
    """
    from porthole_cli import child_env

    env = child_env({"PMB_SUDO": "sudo", "PATH": "/usr/bin"},
                    {"PORTHOLE_ARCH": "aarch64"})
    assert "PMB_SUDO" not in env, env
    assert env.get("PATH") == "/usr/bin", "the rest of the environment stands"


# ------------------------- does $FASTBOOT resolve where builds actually run --
#
# Checking it on the HOST is a different question, and answering the host's is
# what let a `fast` build compile, reach "safe to flash", send the phone to the
# bootloader, and then spend 181.2s insisting it never got there.


class _FakeSandbox:
    CONTAINER = "porthole-sandbox"


def _fastboot_row(monkey_run):
    """Run the workspace fastboot check with podman stubbed."""
    import subprocess as sp
    saved = sp.run
    sp.run = monkey_run
    try:
        ch = doctor.Checks()
        doctor._workspace_fastboot_row(ch, _FakeSandbox())
        return ch.rows[-1]
    finally:
        sp.run = saved


class _Proc:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def test_a_workspace_fastboot_that_resolves_is_ok():
    row = _fastboot_row(lambda *a, **k: _Proc(0, "/usr/bin/fastboot\n"))
    assert row["status"] == "ok", row
    assert "/usr/bin/fastboot" in row["detail"], row


def test_a_host_path_that_the_container_lacks_is_a_failure():
    """127 with empty stdout is byte-for-byte a phone that is NOT in the
    bootloader, which is exactly why doctor has to ask before the build does."""
    row = _fastboot_row(lambda *a, **k: _Proc(127, ""))
    assert row["status"] == "fail", row
    assert "does not resolve inside the workspace" in row["detail"], row
    assert "sandbox" in row["fix"], row["fix"]


def test_a_check_that_could_not_run_is_not_a_finding_about_fastboot():
    """69 doctrine: the check not happening is not the check failing."""
    def boom(*a, **k):
        raise OSError("podman went away")
    row = _fastboot_row(boom)
    assert row["status"] == "warn", row
    assert "could not ask" in row["detail"], row


# ---------------------------------------------- which key is actually used --

def test_doctor_names_the_key_a_pmaports_checkout_came_from():
    """"Which variable is effective" is a question the tool should answer.

    Four things can decide where pmaports is -- a per-device key, the global
    key, pmbootstrap's cache_git, and the default cache path -- and reading the
    documentation cannot tell you which one won on THIS machine. A path with no
    layer beside it leaves the reader to work it out by elimination.
    """
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="doctor-pmaports-"))
    (tmp / "device").mkdir()
    ch = doctor.Checks()
    doctor._check_pmaports(ch, {"PORTHOLE_DEVICE": "google-taimen",
                                "PORTHOLE_PMAPORTS_GOOGLE_TAIMEN": str(tmp)})
    row = _row(ch, "host: pmaports")
    assert row["status"] == "ok", row
    assert str(tmp) in row["detail"], row
    assert "PORTHOLE_PMAPORTS_GOOGLE_TAIMEN" in row["detail"], (
        "the row names the path but not the key that put it there")


def test_a_missing_host_work_dir_is_not_a_warning():
    """On the workspace tier this directory is never created and never needed.

    A yellow row for a tier you are not on is how a working setup starts
    looking broken to somebody who is fine, which is the failure mode this
    whole change exists to remove.
    """
    ch = doctor.Checks()
    doctor._check_host_workdir(ch, {"PORTHOLE_PMB_DIR": "/nonexistent-xyz"})
    row = _row(ch, "host: work dir")
    assert row["status"] == "ok", row
    assert "host builds only" in row["detail"], row


def test_no_device_does_not_open_a_connection_to_the_device():
    """`--no-device` says it skips anything that touches the device, and the
    row it was not skipping cost 5.09 s of a 5.5 s run: the workspace check
    asks the phone whether it accepts the device key, over ssh, with a five
    second connect timeout, on a host whose device is unplugged.

    Asserted by refusing to let ssh exist rather than by timing: a timing
    assertion is flaky on a loaded box and green on a fast one for the wrong
    reason."""
    import porthole_cmd_sandbox as sandbox

    calls = []
    real = sandbox.subprocess.run

    def spy(argv, *a, **kw):
        if argv and str(argv[0]).endswith("ssh"):
            calls.append(argv)
        return real(argv, *a, **kw)

    sandbox.subprocess.run = spy
    try:
        sandbox._container_state(ROOT, {"PHONE": "user@172.16.42.1"},
                                 probe_device=False)
    finally:
        sandbox.subprocess.run = real
    assert not calls, f"--no-device still opened ssh: {calls}"


if __name__ == "__main__":
    sys.exit(main())
