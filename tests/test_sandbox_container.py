#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The sandbox workspace container: image build and lifecycle argv shapes.

Every test here calls a pure function that RETURNS a podman command line.
Nothing invokes podman. That is deliberate: CI has no podman and no device,
and a lifecycle test that needs either would simply be skipped there, which is
the same as not having it (see the Makefile's note on silent skips).
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cmd_sandbox as sb  # noqa: E402


def test_image_tag_is_versioned_and_local():
    tag = sb._image_tag(ROOT)
    version = (ROOT / "VERSION").read_text().strip()
    assert tag == f"localhost/porthole-sandbox:{version}", tag


def test_containerfile_pins_the_base_tag():
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    assert "alpine:latest" not in text, "a sandbox that changes under you is not a sandbox"
    assert "FROM docker.io/library/alpine:3.24" in text, text[:200]


def test_containerfile_asserts_the_load_bearing_pmbootstrap_flag():
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    assert "--no-image" in text, (
        "the zero-privilege design rests on this flag; the BUILD must fail "
        "when a pmbootstrap without it lands, not a device session")


def test_build_argv_tags_the_image_and_points_at_the_containerfile():
    argv = sb._build_argv(ROOT, force=False)
    assert argv[:2] == ["podman", "build"], argv
    assert "-t" in argv and sb._image_tag(ROOT) in argv, argv
    assert str(ROOT / "sandbox" / "Containerfile") in argv, argv
    assert str(ROOT / "sandbox") == argv[-1], argv


def test_build_argv_force_bypasses_the_layer_cache():
    plain = sb._build_argv(ROOT, force=False)
    forced = sb._build_argv(ROOT, force=True)
    assert "--no-cache" not in plain, plain
    assert "--no-cache" in forced, forced


def test_device_key_is_created_private_and_is_idempotent():
    home = pathlib.Path(tempfile.mkdtemp(prefix="porthole-key-test-"))
    key = sb._ensure_device_key(home)
    assert key.exists(), key
    assert key.with_suffix(".pub").exists(), "public half missing"
    assert oct(key.stat().st_mode & 0o777) == "0o600", oct(key.stat().st_mode)
    first = key.read_bytes()
    again = sb._ensure_device_key(home)
    assert again == key and again.read_bytes() == first, "not idempotent"


def test_mounts_never_expose_the_users_ssh_directory():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    for src, dst, _opts in mounts:
        assert not src.rstrip("/").endswith("/.ssh"), (src, dst)
        assert src != str(pathlib.Path.home()), (src, dst)


def test_mounts_carry_the_device_key_read_only():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    key = [m for m in mounts if m[1] == sb.DEVICE_KEY_IN]
    assert len(key) == 1, mounts
    assert key[0][0] == "/k/device_key", key
    assert key[0][2] == "ro", "the key must be mounted read-only"


def test_mounts_include_the_device_mutex_lock():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    lock = sb._lock_path("testdev")
    assert lock == "/tmp/porthole-testdev.lock", lock
    assert any(src == lock for src, _dst, _o in mounts), (
        "without this a containerised agent and a host agent get DIFFERENT "
        "locks and drive the one phone at the same time")


def test_mounts_include_usb_and_the_workdir():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    dsts = [dst for _src, dst, _o in mounts]
    assert "/dev/bus/usb" in dsts, dsts
    assert "/pmb" in dsts, dsts
    assert "/porthole" in dsts, dsts


def test_lock_path_honours_tk_device_lock_override():
    old = os.environ.pop("TK_DEVICE_LOCK", None)
    try:
        assert sb._lock_path("testdev") == "/tmp/porthole-testdev.lock"
        os.environ["TK_DEVICE_LOCK"] = "/custom/lock"
        assert sb._lock_path("testdev") == "/custom/lock"
    finally:
        if old is None:
            os.environ.pop("TK_DEVICE_LOCK", None)
        else:
            os.environ["TK_DEVICE_LOCK"] = old


def test_ensure_device_key_raises_bail_when_ssh_keygen_missing():
    home = pathlib.Path(tempfile.mkdtemp(prefix="porthole-key-test-"))
    old_path = os.environ.get("PATH")
    os.environ["PATH"] = ""
    try:
        try:
            sb._ensure_device_key(home)
            assert False, "expected Bail when ssh-keygen is missing"
        except sb.Bail as exc:
            assert "ssh-keygen" in str(exc), exc
    finally:
        if old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old_path


def _argv_for_test():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    return sb._up_argv(ROOT, "localhost/porthole-sandbox:0.1.0", mounts, "testdev")


def test_up_argv_is_detached_and_named_and_not_ephemeral():
    argv = _argv_for_test()
    assert argv[:3] == ["podman", "run", "-d"], argv
    assert "--rm" not in argv, (
        "the workspace is persistent; --rm is what made the old shell useless")
    assert "--name" in argv and sb.CONTAINER in argv, argv


def test_up_argv_maps_container_root_to_our_uid():
    argv = _argv_for_test()
    assert "--userns=keep-id:uid=0,gid=0" in argv, argv


def test_up_argv_grants_fuse_but_not_host_networking():
    argv = _argv_for_test()
    assert "/dev/fuse" in argv, "fuse2fs is how the image is built without loop"
    assert "--network=host" not in argv, (
        "pasta reaches the device on the default netns; host networking "
        "gives up isolation for nothing")
    assert "--privileged" not in argv, argv


def test_up_argv_renders_every_mount():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    argv = sb._up_argv(ROOT, "img:1", mounts, "testdev")
    for src, dst, opts in mounts:
        assert f"{src}:{dst}:{opts}" in argv, (src, dst, opts)


def test_up_argv_keeps_the_container_alive():
    argv = _argv_for_test()
    assert argv[-1] in ("sleep", "infinity") or "infinity" in argv, argv


def test_mounts_include_the_porthole_user_config_dir_when_it_exists():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-xdg-test-"))
    (tmp / "porthole").mkdir()
    old = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(tmp)
    try:
        mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                            "testdev", [])
        match = [m for m in mounts if m[1] == "/run/porthole/config/porthole"]
        assert len(match) == 1, (
            "without this the container resolves a DIFFERENT device and the "
            "lock mount above becomes decorative: " + repr(mounts))
        assert match[0][0] == str(tmp / "porthole"), match
        assert match[0][2] == "ro", (
            "config.env sets FASTBOOT/ADB and the HOST executes those "
            "values; a writable mount is container-to-host code "
            "execution: " + repr(match))
    finally:
        if old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old


def test_up_argv_sets_xdg_config_home_and_matching_device_lock():
    argv = _argv_for_test()
    assert "-e" in argv and "XDG_CONFIG_HOME=/run/porthole/config" in argv, (
        "without this the in-container load_config never finds the mounted "
        "config, and PORTHOLE_DEVICE resolves differently inside: " + repr(argv))
    lock = sb._lock_path("testdev")
    assert f"TK_DEVICE_LOCK={lock}" in argv, (
        "the container must compute the SAME lock path as the host, or the "
        "bind-mounted lock file protects nothing: " + repr(argv))


def test_up_argv_points_ssh_at_the_mounted_device_key():
    argv = _argv_for_test()
    assert f"PORTHOLE_SSH_KEY={sb.DEVICE_KEY_IN}" in argv, (
        "unset, the device tooling falls back to ~/.ssh/id_ed25519 -- which "
        "the workspace deliberately cannot see: " + repr(argv))
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    assert sb.DEVICE_KEY_IN in [dst for _s, dst, _o in mounts], (
        "the value must name a path that is actually mounted")


def test_up_argv_labels_the_container_with_the_lock_it_baked_in():
    argv = _argv_for_test()
    assert f"{sb.LOCK_LABEL}={sb._lock_path('testdev')}" in argv, (
        "TK_DEVICE_LOCK is baked in at up time and cannot follow a later "
        "`porthole use`; the label is how the drift is noticed: " + repr(argv))
    assert argv[argv.index(f"{sb.LOCK_LABEL}={sb._lock_path('testdev')}") - 1] \
        == "--label", argv


def test_lock_drift_only_fires_on_a_real_mismatch():
    assert sb._lock_drift("/tmp/a.lock", "/tmp/a.lock") == ""
    assert sb._lock_drift("", "/tmp/a.lock") == "", (
        "a container from before the label is unknowable, not drift")
    msg = sb._lock_drift("/tmp/old.lock", "/tmp/new.lock")
    assert "/tmp/old.lock" in msg and "/tmp/new.lock" in msg, msg


def test_exec_argv_omits_tty_when_a_command_is_given():
    argv = sb._exec_argv(["pmbootstrap", "status"], tty=False)
    assert "-it" not in argv, (
        "an agent has no TTY; -it here is why the container tier was "
        "unreachable from an agent")
    assert "-t" not in argv, argv
    assert argv[:2] == ["podman", "exec"], argv
    assert argv[-2:] == ["pmbootstrap", "status"], argv


def test_exec_argv_is_interactive_for_a_human_shell():
    argv = sb._exec_argv(None, tty=True)
    assert "-it" in argv, argv
    assert argv[-1] == "/bin/bash", argv


def test_exec_argv_targets_the_named_container():
    argv = sb._exec_argv(["true"], tty=False)
    assert sb.CONTAINER in argv, argv


def test_down_removes_the_container_but_names_no_volume():
    argv = sb._down_argv()
    assert argv[:3] == ["podman", "rm", "-f"], argv
    assert sb.CONTAINER in argv, argv
    assert "-v" not in argv and "--volumes" not in argv, (
        "the mounts are the user's real directories -- never remove them")


def test_down_message_distinguishes_removed_from_never_running():
    assert "removed" in sb._down_message(True), sb._down_message(True)
    assert "untouched" in sb._down_message(True), (
        "say the mounts survived; that is the reassurance `down` owes")
    assert "not running" in sb._down_message(False), sb._down_message(False)
    assert "removed" not in sb._down_message(False), (
        "`podman rm -f` exits 0 either way -- this claimed a removal that "
        "never happened once already")


def test_device_key_path_has_one_definition():
    home = pathlib.Path(tempfile.mkdtemp(prefix="porthole-key-test-"))
    assert sb._ensure_device_key(home) == home / sb.DEVICE_KEY, (
        "the created key and the path status reports must be the same "
        "constant; two copies of a security-relevant path drift")


def test_container_state_reports_the_workspace():
    state = sb._container_state(ROOT)
    for key in ("podman", "image", "image_built", "container_running",
                "device_key", "issues"):
        assert key in state, (key, sorted(state))
    assert state["image"] == sb._image_tag(ROOT), state["image"]


def test_a_failed_inspect_is_not_read_as_an_absent_label():
    """The drift guard must be loud when it cannot answer.

    `podman inspect` exits 125 with EMPTY stdout when its template names a
    field that no longer exists. Treating that as "no label" would disable
    device-lock drift detection for every container, silently -- and a silent
    pass reads exactly like agreement. It has to refuse instead.
    """
    try:
        sb._lock_from_inspect(125, "", "Error: unknown field .Config.Nope")
    except Exception as exc:
        assert "device-lock label" in str(exc), exc
    else:
        assert False, "a failed inspect was accepted as an absent label"


def test_an_absent_label_is_reported_as_absent_not_as_a_lock():
    for stdout in ("", "   ", "<no value>"):
        assert sb._lock_from_inspect(0, stdout, "") == "", repr(stdout)


def test_a_present_label_comes_back_verbatim():
    assert sb._lock_from_inspect(0, "/tmp/porthole-taimen.lock\n", "") == \
        "/tmp/porthole-taimen.lock"


# ---- the demotion, guarded ------------------------------------------------
#
# These exist because a propagation pass across nine files is exactly the kind
# of work that rots silently: the code ships, the docs keep recommending the
# thing it replaced, and an agent follows the docs. Grep, do not remember.

def test_the_bringup_skill_tells_an_agent_the_workspace_exists():
    text = (ROOT / "skills" / "porthole-bringup" / "SKILL.md").read_text()
    assert "porthole sandbox" in text, (
        "the bring-up skill never mentions the workspace, so an agent loading "
        "it would reach for host root instead")
    assert "sandbox shell" in text, "the skill does not say how to run a command"


def test_agent_facing_docs_name_the_workspace_verbs():
    for rel in ("AGENTS.md", "README.md", "skills/porthole-bringup/SKILL.md"):
        text = (ROOT / rel).read_text()
        assert "sandbox up" in text or "sandbox shell" in text, rel


def test_wherever_the_broker_is_still_named_it_is_marked_as_the_fallback():
    """The broker may be mentioned; it may not be offered as an equal."""
    for rel in ("AGENTS.md", "README.md", "docs/SANDBOX.md"):
        text = (ROOT / rel).read_text()
        if "ph-sudo" not in text and "PMB_SUDO" not in text:
            continue
        low = text.lower()
        assert "legacy" in low or "fallback" in low, (
            f"{rel} names the broker without marking it a fallback")


def test_the_verb_help_does_not_advertise_two_equal_tiers():
    assert "Two tiers" not in sb.SPEC["description"], sb.SPEC["description"]


def test_installing_the_broker_requires_an_explicit_flag():
    flags = [names[0] for names, _kw in sb.SPEC["args"]]
    assert "--broker" in flags, (
        "a plain `sandbox install` must not grant a sudoers entry: " + str(flags))


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
