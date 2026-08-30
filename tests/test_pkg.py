#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole pkg` -- the parts that can be wrong without a build running.

Every assertion here is about a decision made BEFORE or AFTER pmbootstrap
runs: which aport, which command, whether the artifact landed, whether a
status file describes something still alive. None of it needs podman, a
device, or four hours.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

import porthole_cmd_pkg as pkg  # noqa: E402
import porthole_progress as progress  # noqa: E402


APKBUILD = """\
pkgname=phoc
pkgver=0.57.0
pkgrel=50
arch="all"
"""


def _tree(tmp: pathlib.Path):
    """A pmaports-shaped checkout: a flat category and a nested device one."""
    (tmp / "temp" / "phoc").mkdir(parents=True)
    (tmp / "temp" / "phoc" / "APKBUILD").write_text(APKBUILD)
    (tmp / "device" / "testing" / "device-x").mkdir(parents=True)
    (tmp / "device" / "testing" / "device-x" / "APKBUILD").write_text(
        "pkgname=device-x\npkgver=1\npkgrel=35\n")
    return tmp


# ------------------------------------------------------------ resolution --

def test_an_aport_is_found_at_either_depth():
    with tempfile.TemporaryDirectory() as d:
        root = _tree(pathlib.Path(d))
        assert pkg.find_aport(root, "phoc") == root / "temp" / "phoc"
        assert pkg.find_aport(root, "device-x") == \
            root / "device" / "testing" / "device-x"


def test_an_unknown_aport_is_none_rather_than_a_guess():
    with tempfile.TemporaryDirectory() as d:
        assert pkg.find_aport(_tree(pathlib.Path(d)), "nope") is None


def test_the_three_fields_are_read_without_running_the_shell():
    fields = pkg.apkbuild_fields(APKBUILD)
    assert fields == {"pkgname": "phoc", "pkgver": "0.57.0", "pkgrel": "50"}


def test_a_quoted_field_reads_the_same_as_a_bare_one():
    assert pkg.apkbuild_fields('pkgver="1.2.3"\n')["pkgver"] == "1.2.3"


# ------------------------------------------------- the pmbootstrap trap --

def test_a_conditionally_appended_dependency_is_warned_about():
    """The trap that cost a full webkit configure: pmbootstrap parses an
    APKBUILD line by line, so a dependency added inside a case/esac is never
    installed and the build dies inside cmake naming a library apk has."""
    text = APKBUILD + '\ncase "$CARCH" in\n*)\n\tmakedepends="$makedepends '\
                      'libjxl-dev"\n\t;;\nesac\n'
    assert "invisible to it" in pkg.conditional_dep_warning(text)


def test_a_plain_apkbuild_is_not_warned_about():
    assert pkg.conditional_dep_warning(APKBUILD + 'makedepends="meson"\n') == ""


def test_the_warning_names_the_package_rather_than_the_shape():
    text = APKBUILD + 'makedepends="cmake"\ncase "$CARCH" in\n*)\n\t'\
                      'makedepends="$makedepends libjxl-dev"\n\t;;\nesac\n'
    assert "libjxl-dev is appended conditionally" in pkg.conditional_dep_warning(text)


def test_an_aport_already_fixed_is_not_warned_about():
    """The real webkit2gtk-6.0 appends libjxl-dev inside a case/esac AND
    lists it statically, having already been bitten once. Firing on that is a
    warning about correct code, which is how a check trains people to ignore
    it -- and this check has exactly one chance to be believed, ninety seconds
    before a cmake failure that blames something else."""
    text = APKBUILD + 'makedepends="cmake libjxl-dev"\ncase "$CARCH" in\n*)\n\t'\
                      'makedepends="$makedepends libjxl-dev"\n\t;;\nesac\n'
    assert pkg.conditional_dep_warning(text) == ""


def test_a_multi_line_dependency_list_is_read_whole():
    """Dependency lists wrap across lines. Reading only the first would call
    every package after the newline missing."""
    text = 'makedepends="\n\tcmake\n\tlibjxl-dev\n\t"\n'
    assert "libjxl-dev" in pkg.static_deps(text, "makedepends")


# -------------------------------------------------------------- the apk --

def test_the_expected_apk_names_the_version_the_aport_declares():
    with tempfile.TemporaryDirectory() as d:
        packages = pathlib.Path(d)
        (packages / "edge" / "aarch64").mkdir(parents=True)
        want = pkg.expected_apk(packages, "aarch64", pkg.apkbuild_fields(APKBUILD))
        assert want.name == "phoc-0.57.0-r50.apk"
        assert want.parent == packages / "edge" / "aarch64"


def test_an_apkbuild_missing_a_field_names_no_artifact():
    """Better to say we cannot check than to check the wrong filename."""
    assert pkg.expected_apk(pathlib.Path("/tmp"), "aarch64",
                            {"pkgname": "phoc"}) is None


# ----------------------------------------------------------- the command --

def test_the_workspace_build_always_passes_lax():
    """--lax is not a speed knob in the workspace, it is the only thing that
    runs: a non-lax build umounts the chroot and cannot put it back."""
    cmd = pkg.container_cmd("phoc", "aarch64")
    assert "--lax" in " ".join(cmd)
    assert cmd[0] == "podman"
    assert "pmbootstrap build --lax phoc --arch aarch64" in cmd[-1]


def test_pmbootstrap_is_forced_to_flush_its_output():
    """pmbootstrap is Python: into a pipe it block-buffers and says nothing
    until it exits. Five minutes into a real build the log was zero bytes
    while the compiler was visibly running, which makes every bar in this
    verb decorative."""
    assert "PYTHONUNBUFFERED=1" in pkg.container_cmd("phoc", "aarch64")
    assert pkg.UNBUFFERED["PYTHONUNBUFFERED"] == "1"


def test_an_aport_name_reaching_the_shell_is_quoted():
    assert "'a b'" in pkg.container_cmd("a b", "aarch64")[-1]


def test_the_host_build_is_not_lax_unless_asked():
    assert "--lax" not in pkg.host_cmd("phoc", "aarch64", lax=False)
    assert "--lax" in pkg.host_cmd("phoc", "aarch64", lax=True)


# ------------------------------------------------------------- outdated --

def _apk(packages: pathlib.Path, name: str, when=None):
    path = packages / "edge" / "aarch64" / f"{name}.apk"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    if when:
        import os
        os.utime(path, (when, when))
    return path


def test_an_aport_never_built_here_is_not_reported():
    """Quietness is the feature. temp/ carries eighteen forks; listing the
    ones nobody has built would train everyone to ignore the check."""
    with tempfile.TemporaryDirectory() as d:
        root = _tree(pathlib.Path(d) / "aports")
        packages = pathlib.Path(d) / "packages"
        packages.mkdir()
        assert pkg.outdated(root, packages, "aarch64") == []


def test_a_bumped_pkgrel_with_no_matching_apk_is_reported():
    with tempfile.TemporaryDirectory() as d:
        root = _tree(pathlib.Path(d) / "aports")
        packages = pathlib.Path(d) / "packages"
        _apk(packages, "phoc-0.57.0-r49")
        found = dict(pkg.outdated(root, packages, "aarch64"))
        assert "phoc" in found and "0.57.0-r50" in found["phoc"]


def test_an_edited_aport_is_reported_even_at_the_same_version():
    """The phoc case: the apk exists at the right version, but a patch beside
    the APKBUILD changed after it was built, so what ships is not what you
    edited."""
    with tempfile.TemporaryDirectory() as d:
        root = _tree(pathlib.Path(d) / "aports")
        packages = pathlib.Path(d) / "packages"
        _apk(packages, "phoc-0.57.0-r50", when=time.time() - 600)
        (root / "temp" / "phoc" / "fix.patch").write_text("diff")
        found = dict(pkg.outdated(root, packages, "aarch64"))
        assert found.get("phoc") == "edited since it was last built"


def test_an_untouched_aport_is_silent():
    with tempfile.TemporaryDirectory() as d:
        root = _tree(pathlib.Path(d) / "aports")
        packages = pathlib.Path(d) / "packages"
        _apk(packages, "phoc-0.57.0-r50", when=time.time() + 600)
        assert dict(pkg.outdated(root, packages, "aarch64")).get("phoc") is None


def test_a_subpackage_apk_does_not_invent_an_aport():
    with tempfile.TemporaryDirectory() as d:
        root = _tree(pathlib.Path(d) / "aports")
        packages = pathlib.Path(d) / "packages"
        _apk(packages, "phoc-dev-0.57.0-r50")
        assert dict(pkg.outdated(root, packages, "aarch64")).get("phoc-dev") is None


def test_a_pkgname_built_from_a_variable_resolves():
    """Kernel aports almost universally write `pkgname=linux-$_flavor`.
    Reading that literally makes every downstream filename wrong, and a wrong
    filename does not fail loudly -- it reports that a package which built
    perfectly well was never built. Caught on the real taimen kernel aport,
    where `builds` said not-built while r21 sat in the package dir."""
    text = ('_flavor="postmarketos-qcom-msm8998-7.2"\n'
            "pkgname=linux-$_flavor\npkgver=7.2.2\npkgrel=21\n")
    fields = pkg.apkbuild_fields(text)
    assert fields["pkgname"] == "linux-postmarketos-qcom-msm8998-7.2"
    assert pkg.expected_apk(pathlib.Path("/x"), "aarch64", fields).name == \
        "linux-postmarketos-qcom-msm8998-7.2-7.2.2-r21.apk"


def test_braced_variables_resolve_too():
    fields = pkg.apkbuild_fields('_f="x"\npkgname=linux-${_f}\n')
    assert fields["pkgname"] == "linux-x"


def test_a_field_that_cannot_be_resolved_is_dropped_not_guessed():
    """Absent is a state the callers handle ("cannot check"). Wrong is not:
    it reports a built package as unbuilt."""
    assert "pkgname" not in pkg.apkbuild_fields("pkgname=linux-$undefined\n")
    assert "pkgname" not in pkg.apkbuild_fields("pkgname=$(uname -r)\n")


def test_stopping_kills_both_sides_not_just_the_client():
    """`podman exec` and the process it exec'd are different processes.
    Killing only the client leaves pmbootstrap compiling inside, still holding
    the buildroot -- which is precisely the state that destroys the next
    build. Cancelling had to be done by hand, twice, in one session."""
    class FakeSnap(dict):
        pass

    result = pkg.stop_plan(FakeSnap({"state": "running", "pid": 4242}))
    assert result == ("kill", 4242), result
    assert pkg.stop_plan(FakeSnap({"state": "done", "pid": 4242})) == ("none", 0)
    assert pkg.stop_plan(FakeSnap({})) == ("none", 0)


def test_there_is_one_package_build_implementation():
    """`aports build` and `pkg build` both built packages, but aports build
    called raw pmbootstrap: no progress bar, no --lax handling, no artifact
    check, no detach. Two doors and only one of them good is worse than one
    door, and the redfin developer used neither."""
    import inspect

    import porthole_cmd_aports as aports

    source = inspect.getsource(aports.cmd_build)
    assert "porthole_cmd_pkg" in source or "_delegate_to_pkg" in source, \
        "aports build still has its own build implementation"


def test_force_reaches_pmbootstrap():
    """A declared flag that silently does nothing is worse than no flag.
    `aports build --force` became a no-op when it started delegating."""
    assert "--force" in pkg.container_cmd("phoc", "aarch64", force=True)[-1]
    assert "--force" not in pkg.container_cmd("phoc", "aarch64")[-1]
    assert "--force" in pkg.host_cmd("phoc", "aarch64", lax=False, force=True)
    assert "--force" not in pkg.host_cmd("phoc", "aarch64", lax=False)


def test_a_patch_missing_from_source_is_named():
    """E3 from the redfin report: a patch listed only in patches= gets no
    checksum, because pmbootstrap checksums what is in source=. The fix
    already existed as `porthole aports patch` and was never found, so the
    session hand-edited and lost time. Name it where the mistake happens."""
    import porthole_cmd_aports as aports
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        directory = pathlib.Path(d)
        (directory / "fix.patch").write_text("diff")
        (directory / "other.patch").write_text("diff")
        apkbuild = 'source="foo.tar.gz\n\tfix.patch\n\t"\n'
        missing = aports.untracked_patches(directory, apkbuild)
        assert missing == ["other.patch"], missing


def test_the_brief_can_say_whether_a_build_is_running():
    """The first question when picking up a handoff, and the one two agents
    collided over in this repo. Answering it used to mean hand-rolling
    `podman exec ps` and a lock probe."""
    import porthole_cmd_brief as brief

    summary = brief.activity_summary(
        {"state": "running", "rung": "pkg:webkit2gtk-6.0", "pid": 1,
         "elapsed": 900.0, "progress": 0.32, "eta": None, "started": 0.0},
        holder="webkit2gtk-6.0 pid=123 since=13:52:27",
        alive=lambda _: True)
    assert "webkit2gtk-6.0" in summary
    assert "running" in summary


def test_the_brief_says_idle_when_nothing_is_building():
    import porthole_cmd_brief as brief

    assert "idle" in brief.activity_summary({}, holder="", alive=lambda _: False)


def test_every_build_says_how_to_watch_it_not_only_a_detached_one():
    """The developer could only watch a build if the agent happened to use
    --detach. Run as a background task -- which AGENTS.md tells agents to do --
    the bar goes to a log the human never sees, so nothing ever told them how
    to look."""
    assert "porthole pkg watch" in pkg.watch_hint(tty=True)
    assert "porthole pkg watch" in pkg.watch_hint(tty=False)
    # Loudest exactly when the human cannot see the bar.
    assert len(pkg.watch_hint(tty=False)) > len(pkg.watch_hint(tty=True))


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
