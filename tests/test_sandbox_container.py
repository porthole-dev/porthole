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
