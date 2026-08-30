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

    alive = lambda pid: pid == 4242  # noqa: E731
    result = pkg.stop_plan(FakeSnap({"state": "running", "pid": 4242}), alive)
    assert result == ("kill", 4242), result
    assert pkg.stop_plan(FakeSnap({"state": "done", "pid": 4242}), alive) == ("none", 0)
    assert pkg.stop_plan(FakeSnap({}), alive) == ("none", 0)


def test_stopping_does_not_signal_a_recycled_pid():
    """`state: running` is a claim, not a fact: a SIGKILLed build or a reboot
    leaves it set with a pid the OS is then free to reuse, and stop_plan
    trusted it -- so `pkg stop` could SIGTERM an unrelated process. The
    docstring promised the protection the code did not implement."""
    dead = {"state": "running", "pid": 4242}
    assert pkg.stop_plan(dead, lambda pid: False) == ("none", 0)
    assert pkg.stop_plan(dead, lambda pid: True) == ("kill", 4242)


def test_stopping_with_no_status_file_writes_nothing():
    """With no status file snap is {}, and _stop wrote it back anyway --
    publishing `{"state": "failed"}` for a build that never ran, which
    `pkg status` and `brief` then reported as the answer."""
    class Out:
        def __call__(self, *a):
            pass

        def paint(self, text, _colour=""):
            return text

    class Ctx:
        out = Out()

        def __init__(self, rundir):
            self.cfg = {"PORTHOLE_RUNDIR": str(rundir)}
            self.root = rundir

        def emit(self, payload, render):
            render()
            return payload

    with tempfile.TemporaryDirectory() as d:
        rundir = pathlib.Path(d)
        # Stubbed so the assertion is about the status file and not about
        # whether this machine happens to have podman up.
        saved = (pkg._kill_inside, pkg._pmb_workdir, pkg.build_module)
        pkg._kill_inside = lambda: None
        pkg._pmb_workdir = lambda ctx, usable: rundir
        pkg.build_module = lambda: type(
            "B", (), {"_workspace_usable": staticmethod(lambda ctx: (False, "test"))})
        try:
            pkg._stop(Ctx(rundir))
        finally:
            pkg._kill_inside, pkg._pmb_workdir, pkg.build_module = saved
        assert not (rundir / "pkg-status.json").exists(), \
            "_stop invented a failed build that never ran"


def test_detach_forwards_the_flags_that_change_the_build():
    """`--detach` rebuilds the argv by hand, so anything not listed is
    silently dropped. It dropped `--force` (a declared flag doing nothing) and
    `--wait` (the child then bailed EX_LOCK into the spawn log while the
    parent had already returned EX_OK and armed `pkg watch`)."""
    class Args:
        timeout, force, wait = 3600, True, 600.0

    argv = pkg.detach_argv("/w/bin/porthole", "webkit2gtk-6.0", "aarch64", Args())
    assert "--force" in argv, argv
    assert argv[argv.index("--wait") + 1] == "600.0", argv

    class Plain:
        timeout, force, wait = 3600, False, 0.0

    bare = pkg.detach_argv("/w/bin/porthole", "phoc", "aarch64", Plain())
    assert "--force" not in bare and "--wait" not in bare, bare


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


def test_watch_never_starts_with_a_blank_screen():
    """A watch that prints nothing for thirty seconds is indistinguishable
    from a hang, and is why the developer stopped trusting it. Reproduced:
    `timeout 5 porthole pkg watch` produced zero output when the status file
    held an already-finished build."""
    stale = {"rung": "pkg:gst-plugins-good", "state": "done", "pid": 1,
             "elapsed": 795.0, "started": 1000.0}
    said = pkg.waiting_line(stale, now=2000.0)
    assert said and "gst-plugins-good" in said
    assert pkg.waiting_line(None, now=2000.0), "no status file must still say something"


def test_watch_can_be_left_open_on_a_terminal_but_not_on_a_pipe():
    """`watch` is advertised as costing nothing to leave open, and a 30s
    ceiling defeated that: opened in a second terminal before an agent starts
    a build, it exited before the build began. A person can Ctrl-C; a pipe
    cannot, and an agent that ran this by accident would hang forever."""
    assert pkg.wait_ceiling(tty=True, now=1000.0) is None
    assert pkg.wait_ceiling(tty=False, now=1000.0) == 1030.0


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



# ---------------------------------------------------------- two trees --

def _two_trees(tmp: pathlib.Path):
    """A pmaports and an aports_upstream, laid out as pmbootstrap clones them.

    `phoc` lives in BOTH, which is what a fork looks like on disk and the one
    case where the two trees disagree about the answer.
    """
    pm = tmp / "cache_git" / "pmaports"
    up = tmp / "cache_git" / "aports_upstream"
    for rel in ("temp/phoc", "main/hello", "device/testing/device-x",
                "extra-repos/systemd/phosh"):
        (pm / rel).mkdir(parents=True)
        (pm / rel / "APKBUILD").write_text(f"pkgname={rel.rsplit('/', 1)[1]}\n"
                                           f"pkgver=1.0\npkgrel=7\n")
    (pm / "docs").mkdir()                      # a category-shaped non-category
    (pm / "docs" / "notes.md").write_text("x")
    for rel in ("community/phoc", "community/phosh", "main/hello",
                "community/gnome-calculator", "testing/calculator-x"):
        (up / rel).mkdir(parents=True)
        (up / rel / "APKBUILD").write_text(f"pkgname={rel.split('/')[1]}\n"
                                           f"pkgver=2.0\npkgrel=1\n")
    return pm, up


def test_a_tree_is_scanned_at_both_depths_and_docs_is_not_a_package():
    """A package is a directory holding an APKBUILD -- not a directory sitting
    under something that looked like a category. pmaports keeps docs/ beside
    temp/, and Alpine keeps scripts/ beside community/."""
    with tempfile.TemporaryDirectory() as d:
        pm, _ = _two_trees(pathlib.Path(d))
        found = pkg.scan_tree(pm)
        assert set(found) == {"phoc", "hello", "device-x", "phosh"}
        assert found["device-x"].parent.name == "testing"     # two deep
        assert found["phosh"].parent.name == "systemd"        # two deep


def test_scanning_nothing_is_empty_not_an_error():
    """`find_aports_upstream` returns None on a host that never cloned it, and
    a search must still answer for pmaports rather than raise."""
    assert pkg.scan_tree(None) == {}


def test_a_package_in_both_trees_is_reported_as_pmaports():
    """pmaports shadows Alpine, and pmaports is the tree `pmbootstrap build`
    reads -- so saying `phoc` is Alpine's would send someone to fork a package
    they have already forked."""
    with tempfile.TemporaryDirectory() as d:
        pm, up = _two_trees(pathlib.Path(d))
        hits, guessed = pkg.search(pm, up, "phoc")
        assert not guessed
        assert [(h["name"], h["tree"]) for h in hits] == [("phoc", "pmaports")]
        assert hits[0]["where"] == "temp/"


def test_a_near_miss_answers_with_the_package_rather_than_silence():
    """`porthole pkg build posh` is the search this verb came from. `posh` is
    not a substring of `phosh`, so a substring match answers a real question
    with nothing at all."""
    with tempfile.TemporaryDirectory() as d:
        pm, up = _two_trees(pathlib.Path(d))
        hits, guessed = pkg.search(pm, up, "posh")
        assert guessed
        assert hits[0]["name"] == "phosh"


def test_the_closest_hit_is_first_because_the_hint_names_it():
    """Alphabetical put `calculator-x` above `gnome-calculator` for the search
    `calculator`, and the follow-up hint offers to fork whatever is first."""
    with tempfile.TemporaryDirectory() as d:
        pm, up = _two_trees(pathlib.Path(d))
        hits, _ = pkg.search(pm, up, "hello")
        assert hits[0]["name"] == "hello" and hits[0]["tree"] == "pmaports"
        names = [h["name"] for h in pkg.search(pm, up, "calculator")[0]]
        assert names == ["calculator-x", "gnome-calculator"]


def test_version_is_read_from_the_apkbuild_of_a_hit():
    with tempfile.TemporaryDirectory() as d:
        pm, _ = _two_trees(pathlib.Path(d))
        assert pkg.apkbuild_version(pm / "temp" / "phoc") == "1.0-r7"
        assert pkg.apkbuild_version(pm / "temp" / "nope") == ""


# ------------------------------------------------- the failure message --

def test_an_alpine_package_is_named_as_alpines_not_as_absent():
    """The whole dead end: `pkg build gnome-calculator` reported a real failure
    and then pointed at `porthole aports`, which lists the packages named after
    your device and could never have found it."""
    with tempfile.TemporaryDirectory() as d:
        pm, up = _two_trees(pathlib.Path(d))
        message, hint = pkg.missing_aport_hint(pm, up, "gnome-calculator")
        assert "Alpine's (community/)" in message
        assert hint.startswith("porthole pkg fork gnome-calculator --yes")


def test_a_typo_is_answered_with_the_name_that_was_meant():
    with tempfile.TemporaryDirectory() as d:
        pm, up = _two_trees(pathlib.Path(d))
        message, hint = pkg.missing_aport_hint(pm, up, "posh")
        assert message == "no aport named posh"
        assert "did you mean phosh?" in hint


def test_a_name_in_neither_tree_still_points_somewhere_real():
    """Never a dead end: the fallback names a command that searches both
    trees, rather than one that searches this device's packages."""
    with tempfile.TemporaryDirectory() as d:
        pm, up = _two_trees(pathlib.Path(d))
        message, hint = pkg.missing_aport_hint(pm, up, "zzzqqq")
        assert message == "no aport named zzzqqq"
        assert "porthole pkg search zzzqqq" in hint


def test_the_upstream_tree_is_found_beside_pmaports():
    """Derived, not configured: pmbootstrap puts both in one cache_git/."""
    import porthole_pmaports as pmap

    with tempfile.TemporaryDirectory() as d:
        pm, up = _two_trees(pathlib.Path(d))
        assert pmap.find_aports_upstream(pm) == up
        assert pmap.find_aports_upstream(pathlib.Path(d) / "nowhere") is None


# ------------------------------------------------------- where fork runs --

def test_a_fork_runs_in_the_workspace_not_on_the_host():
    """aportgen belongs in the container for the same reason a build does,
    and it took a real attempt to find out: on the host it asks the privilege
    broker to copy an APKINDEX into a cache directory that does not exist
    yet, ph-sudo refuses the unresolvable destination, and the fork dies at
    exit 78 having written nothing."""
    cmd = pkg.fork_cmd("gnome-calculator")
    assert cmd[0] == "podman" and cmd[1] == "exec"
    assert "aportgen --fork-alpine gnome-calculator" in cmd[-1]
    assert "PYTHONUNBUFFERED=1" in cmd


def test_a_fork_of_an_odd_name_is_quoted():
    """The name reaches a `bash -lc` line, so it is the one argument that
    must never be pasted in raw."""
    assert "'a b'" in pkg.fork_cmd("a b")[-1]


def test_build_and_fork_share_one_container_wrapper():
    """Two podman lines that drift apart is two places to fix the buffering
    bug that `in_container` exists to document."""
    assert pkg.in_container("x")[:-1] == pkg.container_cmd("p", "aarch64")[:-1]


if __name__ == "__main__":
    sys.exit(main())
