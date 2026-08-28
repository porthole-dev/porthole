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
        assert match[0][2] == "rw", match
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
