#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The sandbox workspace container: image build and lifecycle argv shapes.

Every test here calls a pure function that RETURNS a podman command line.
Nothing invokes podman. That is deliberate: CI has no podman and no device,
and a lifecycle test that needs either would simply be skipped there, which is
the same as not having it (see the Makefile's note on silent skips).
"""
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
