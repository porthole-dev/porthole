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
import re
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


class _FakeCtx:
    """A ctx good enough to drive `_run`/`_stream` without a real profile:
    cfg, out, root. Shared here so each test does not hand-roll its own."""

    class _FakeOut:
        def __call__(self, *a, **k):
            pass

        def paint(self, s, _color):
            return s

    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.out = _FakeCtx._FakeOut()
        self.root = ROOT


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


def test_a_preview_names_what_is_missing_instead_of_refusing():
    """A profile that cannot build yet is the normal state of a new port.
    Refusing to describe the build because the build cannot run withholds the
    answer exactly when it is wanted -- and it broke CI, where pmbootstrap is
    not installed."""
    rc, out, err = run("-d", "google-cheetah", "build")
    assert rc == 0, f"a preview must not fail: {err}"
    assert "would build" in out
    assert "missing first" in out, out


def test_build_reports_every_missing_value_at_once():
    """An envkernel build is minutes long. Finding out about the second
    missing key after fixing the first is how an afternoon goes."""
    rc, out, err = run("-d", "google-cheetah", "build", "--yes")
    assert rc != 0
    assert "cannot build yet" in (out + err)


def _fake_workdir(populated: bool):
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp(prefix="porthole-pmb-"))
    chroot = root / "chroot_rootfs_google-taimen"
    if populated:
        info = chroot / "usr" / "share" / "deviceinfo"
        info.mkdir(parents=True)
        (info / "deviceinfo").write_text("deviceinfo_format_version=0\n")
    else:
        chroot.mkdir(parents=True)
    return root


def test_export_rungs_refuse_an_uninstalled_rootfs_chroot():
    """One is_file() instead of twenty minutes.

    `fast` compiled a kernel for 20m35s and then died in `pmbootstrap export`
    because the workspace's rootfs chroot had never had a full install. The
    workspace keeps its own pmbootstrap work dir; installs done on the host do
    not populate it.
    """
    import porthole_cmd_build as build

    problems = build.export_problems(_fake_workdir(populated=False),
                                     "google-taimen")
    assert problems, "an empty rootfs chroot must be refused"
    assert "install" in problems[0], problems


def test_export_rungs_accept_a_populated_rootfs_chroot():
    import porthole_cmd_build as build

    assert build.export_problems(_fake_workdir(populated=True),
                                 "google-taimen") == []


def test_only_the_export_rungs_are_checked():
    # `mod` and `boot` never call pmbootstrap export, so an empty rootfs
    # chroot is irrelevant to them and refusing would be a false stop.
    import porthole_cmd_build as build

    assert "fast" in build.EXPORT_RUNGS
    assert "upgrade" in build.EXPORT_RUNGS
    assert "mod" not in build.EXPORT_RUNGS
    assert "boot" not in build.EXPORT_RUNGS


def test_kernel_is_not_gated_on_its_own_precondition():
    """`kernel` (tkbuild) runs `pmbootstrap install` before `export` --
    ph-build.sh:~821 -- which is what CREATES and populates the rootfs
    chroot export_problems() checks for. Gating `kernel` on that chroot
    already existing made `porthole build kernel --yes` refuse with the
    exact advice it was told to follow: "Run `porthole build kernel --yes`
    once against this work dir." A real session hit that loop -- `fast`
    refused for lack of an installed chroot, and `kernel`, the only rung
    that can install one, refused for the same reason.
    """
    import porthole_cmd_build as build

    assert "kernel" not in build.EXPORT_RUNGS, (
        "kernel populates the chroot itself; gating it on the chroot "
        "already existing is the circular refusal this test guards against")


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


# ------------------------------------------------------------- authorship --

def _repo(tmp):
    import subprocess as sp
    repo = pathlib.Path(tmp)
    sp.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], capture_output=True)
    sp.run(["git", "-C", str(repo), "config", "user.email", "me@example.com"],
           capture_output=True)
    sp.run(["git", "-C", str(repo), "config", "user.name", "Me"], capture_output=True)
    return repo


def _commit(repo, subject, body, author):
    import subprocess as sp
    name, email = author.split(" <")[0], author.split("<")[1].rstrip(">")
    env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": "Me", "GIT_COMMITTER_EMAIL": "me@example.com"}
    (repo / "f.txt").write_text(subject)
    sp.run(["git", "-C", str(repo), "add", "-A"], capture_output=True)
    sp.run(["git", "-C", str(repo), "commit", "-q", "-m", f"{subject}\n\n{body}"],
           env=env, capture_output=True)


def test_a_patch_authored_by_someone_else_is_caught():
    """taimen's audit found all 41 of its patches attributed to one author,
    including Caleb Connolly's and Yassine Oudjana's work -- and the old check
    passed the series, because it tested whether the WORD "Signed-off-by"
    appeared anywhere in the range.

    Sending someone else's patch under your name is the highest-severity
    mistake either port made, and it is invisible in a diff."""
    import subprocess as sp
    sys.path.insert(0, str(ROOT / "lib"))
    import porthole_cmd_aports as ap
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo(tmp)
        _commit(repo, "base: start", "", "Me <me@example.com>")
        sp.run(["git", "-C", str(repo), "tag", "base"], capture_output=True)
        _commit(repo, "pkg: someone else's work",
                "Signed-off-by: Me <me@example.com>",
                "Caleb Connolly <caleb@example.com>")
        found = ap._authorship(repo, "base")
    assert any(s == "fail" for s, _ in found), found
    assert any("AUTHOR is not among the sign-offs" in text for _, text in found)


def test_a_correctly_attributed_series_passes():
    import subprocess as sp
    sys.path.insert(0, str(ROOT / "lib"))
    import porthole_cmd_aports as ap
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo(tmp)
        _commit(repo, "base: start", "", "Me <me@example.com>")
        sp.run(["git", "-C", str(repo), "tag", "base"], capture_output=True)
        _commit(repo, "pkg: my own work", "Signed-off-by: Me <me@example.com>",
                "Me <me@example.com>")
        found = ap._authorship(repo, "base")
    assert not any(s == "fail" for s, _ in found), found
    assert any(s == "ok" for s, _ in found), found


def test_an_unsigned_commit_is_reported():
    import subprocess as sp
    sys.path.insert(0, str(ROOT / "lib"))
    import porthole_cmd_aports as ap
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo(tmp)
        _commit(repo, "base: start", "", "Me <me@example.com>")
        sp.run(["git", "-C", str(repo), "tag", "base"], capture_output=True)
        _commit(repo, "pkg: unsigned", "", "Me <me@example.com>")
        found = ap._authorship(repo, "base")
    assert any("no Signed-off-by" in text for _, text in found), found


# ------------------------------------------------- the ladder is reachable --

def test_every_action_names_a_function_that_exists():
    """`fast` mapped to `tkfast` for the whole life of the verb, and tkfast has
    never existed -- the function is `tkbuild-kernel`. So `porthole build fast`
    died with "command not found" and the only fast rung anyone could reach was
    the slow one. A name is not a link; assert the target is really there."""
    import porthole_cmd_build as B
    src = (ROOT / "tools" / "ph-build.sh").read_text()
    for action, (func, _) in B.ACTIONS.items():
        assert re.search(rf"^{re.escape(func)}\(\)", src, re.M), \
            f"action {action!r} calls {func}(), which ph-build.sh does not define"


def test_auto_measures_without_packaging():
    """`auto` calls into ph-build.sh to answer one question -- which files did
    make touch -- and answers it with _changed_artifacts, which globs .output
    for .ko/.dtb/Image.gz and never opens an apk. So the packaging step at the
    end of _ph_make was pure cost there: 14.66 s per preview, measured
    2026-08-29, and it wrote a `_p` apk every time. `_p` apks outrank release
    builds, which is what _ph_assert_no_devpkgs exists to refuse -- so the
    preview was manufacturing the hazard the rungs guard against. One measure
    pass took the local repo's `_p` count from 1 to 2."""
    import inspect
    import porthole_cmd_build as B
    src = inspect.getsource(B._auto)
    assert '"_ph_measure"' in src, "auto must measure with the non-packaging path"
    assert '"_ph_make"' not in src, "auto must not run the packaging path"


def test_a_worktree_path_is_translated_for_the_container():
    """ph-build prints "set PORTHOLE_KERNEL_TREE to build a worktree" on every
    run, and that knob is a host path -- it stopped at the container boundary
    like every other one, so the workspace silently built the DEFAULT tree
    instead of the worktree you named. A worktree kept inside the device repo
    is mounted at /work and can be translated; one outside is not reachable at
    all and must be dropped rather than sent as a path that does not exist."""
    import porthole_cmd_build as B
    assert B._tree_inside("/repo/linux-ws", "/repo") == "/work/linux-ws"
    assert B._tree_inside("/repo", "/repo") == "/work"
    assert B._tree_inside("/elsewhere/linux", "/repo") == ""
    assert B._tree_inside("", "/repo") == ""
    assert B._tree_inside("/repo/linux", "") == ""


def test_the_container_argv_carries_the_translated_tree_only():
    """NAME=value, not NAME: the value is the CONTAINER's path. Sending it the
    other way would take it from our environment, which is the host path."""
    import porthole_cmd_build as B
    argv = B._container_cmd("_ph_measure", None, {}, "/work/linux-ws")
    assert "PORTHOLE_KERNEL_TREE=/work/linux-ws" in argv
    plain = B._container_cmd("_ph_measure", None, {}, "")
    assert not any("PORTHOLE_KERNEL_TREE" in a for a in plain)


def test_the_cheap_rungs_are_in_the_verb_table():
    """tkmod (~40s) and tkboot (~40s) existed only as shell functions no verb
    named, so an agent reading the table found the ~10 minute rung first."""
    import porthole_cmd_build as B
    assert "mod" in B.ACTIONS and "boot" in B.ACTIONS


def test_mod_refuses_without_both_arguments():
    """One argument builds every module in the tree before failing on the path."""
    rc, out, err = run("-d", DEV, "build", "mod", "just-one.ko", "--yes")
    assert rc != 0
    assert "path and its name" in (out + err)


def test_a_rung_that_takes_no_arguments_says_so():
    rc, out, err = run("-d", DEV, "build", "fast", "stray-arg", "--yes")
    assert rc != 0
    assert "no extra arguments" in (out + err)


def test_the_preview_prints_the_ladder():
    """The expensive mistake is iterating on the wrong rung, so every preview
    shows what the cheaper ones cover."""
    rc, out, _ = run("-d", DEV, "build", "kernel")
    assert rc == 0
    for rung in ("mod", "boot", "fast"):
        assert rung in out, f"the preview never mentions the {rung} rung"


def test_the_fast_rungs_wait_instead_of_handing_back_mid_reboot():
    """tkboot ended at `fastboot boot` and tkflash-boot at `fastboot reboot`,
    so the caller had nothing to poll and wrote `sleep 60` -- wrong in both
    directions per brain/laws/poll-never-sleep.md."""
    src = (ROOT / "tools" / "ph-build.sh").read_text()
    for func in ("tkboot", "tkflash-boot"):
        body = src.split(f"\n{func}() {{", 1)[1].split("\n}\n", 1)[0]
        assert "_ph_wait_up" in body, f"{func} still hands back mid-reboot"
    assert "tk_wait_ssh" in src.split("_ph_wait_up() {", 1)[1][:800], \
        "_ph_wait_up must poll via tk_wait_ssh, not sleep"


def test_tkmod_proves_the_new_module_is_the_running_one():
    """insmod exiting 0 does not mean the old module unloaded."""
    src = (ROOT / "tools" / "ph-build.sh").read_text()
    body = src.split("\ntkmod() {", 1)[1].split("\n}\n", 1)[0]
    assert "srcversion" in body, "tkmod does not verify which build is loaded"


def test_a_build_that_cannot_finish_is_refused_before_it_starts():
    """ENOSPC at minute forty costs the whole build. /proc reports zero free
    space and always exists, so it is a stable stand-in for a full disk."""
    import porthole_cmd_build as build
    problems = build._space_problems({"PORTHOLE_PMB_DIR": "/proc"})
    assert problems, "a disk with no free space was not refused"
    assert "PORTHOLE_PMB_DIR" in problems[0], (
        "the refusal must name the knob that relocates the workdir: "
        + problems[0])


def test_plenty_of_space_is_neither_refused_nor_warned_about():
    import porthole_cmd_build as build
    root = "/"
    if build._free_gb(root) < build.SPACE_WARN_GB:
        return  # this machine genuinely is tight; nothing to assert
    cfg = {"PORTHOLE_PMB_DIR": root}
    assert not build._space_problems(cfg)
    assert not build._space_warning(cfg)


def test_a_build_routes_into_the_workspace_by_default():
    import porthole_cmd_build as build
    argv = build._container_cmd("tkbuild", None, {})
    assert argv[:2] == ["podman", "exec"], argv
    assert argv[-3] == "/bin/bash" and argv[-2] == "-lc", argv
    assert "cd /porthole" in argv[-1] and "tkbuild" in argv[-1], argv[-1]


def test_the_rootfs_password_never_reaches_the_podman_argv():
    """`-e NAME=value` would put it where `ps` shows it to every user on the
    box. `-e NAME` makes podman read it from our environment instead."""
    import porthole_cmd_build as build
    argv = build._container_cmd("tkbuild", None, {"TK_PMOS_PASSWORD": "hunter2"})
    assert "hunter2" not in " ".join(argv), argv
    assert "TK_PMOS_PASSWORD" in argv, argv


def test_host_paths_are_not_resent_into_the_container():
    """The container's PORTHOLE_WORKDIR is /work, set when it was created. The
    host's names a directory that does not exist in there, and sending it was
    what made a real session refuse with 'this profile cannot build yet'."""
    import porthole_cmd_build as build
    argv = build._container_cmd("tkbuild", None,
                                {"TK_X": "1", "PORTHOLE_WORKDIR": "/host/tree"})
    assert "PORTHOLE_WORKDIR" not in argv, argv


def test_module_arguments_survive_the_trip():
    import porthole_cmd_build as build
    argv = build._container_cmd("tkmod", ["drivers/media/i2c/imx179.ko", "imx179"], {})
    assert "imx179" in argv[-1] and "imx179.ko" in argv[-1], argv[-1]


def test_the_host_path_is_still_available():
    import pathlib as _p
    import porthole_cmd_build as build
    argv = build._host_cmd(_p.Path("/repo/tools/ph-build.sh"), "tkclean", None)
    assert argv[0] == "bash", argv
    assert "ph-build.sh" in argv[-1] and "tkclean" in argv[-1], argv[-1]


# ---- rung selection: the decision that must not be wrong -------------------
#
# Measured, not inferred. A header edit moves every module's CRC without
# looking like a config change, and a Kconfig edit can flip a module to
# built-in; both fool a diff reader and neither fools "what did make write".

def test_nothing_rebuilt_means_nothing_to_do():
    import porthole_cmd_build as build
    rung, _args, why = build._classify([])
    assert rung is None, rung
    assert "nothing" in why.lower(), why


def test_one_module_takes_the_cheap_rung():
    import porthole_cmd_build as build
    rung, args, _why = build._classify(["drivers/media/i2c/imx179.ko"])
    assert rung == "mod", rung
    assert args == ["drivers/media/i2c/imx179.ko", "imx179"], args


def test_several_modules_do_not_take_the_module_rung():
    """brain/traps/pushing-one-module-of-a-pair-corrupts-the-other.md --
    modules built together share a struct layout, so pushing a subset
    corrupts the ones left behind."""
    import porthole_cmd_build as build
    rung, _args, why = build._classify(["drivers/a.ko", "drivers/b.ko"])
    assert rung == "fast", rung
    assert "subset" in why or "together" in why, why


def test_only_a_dtb_takes_the_boot_rung():
    import porthole_cmd_build as build
    rung, _a, _w = build._classify(["arch/arm64/boot/dts/qcom/x.dtb"])
    assert rung == "boot", rung


def test_a_moved_image_forces_a_flash_because_crcs_move_with_it():
    """The expensive case that MUST not be optimised away: a rebuilt kernel
    refuses every module already on the phone."""
    import porthole_cmd_build as build
    rung, _a, why = build._classify(
        ["arch/arm64/boot/Image.gz", "drivers/a.ko"])
    assert rung == "fast", rung
    assert "CRC" in why or "crc" in why, why


def test_a_dtb_and_a_module_together_take_the_covering_rung():
    import porthole_cmd_build as build
    rung, _a, _w = build._classify(
        ["arch/arm64/boot/dts/qcom/x.dtb", "drivers/a.ko"])
    assert rung == "fast", rung


def test_the_default_action_is_no_longer_the_most_expensive_rung():
    """A bare `porthole build --yes` used to mean `kernel` (~10m) when `mod`
    (~40s) usually covered it -- 15x, by default, for the command an agent
    reaches for first."""
    import inspect
    import porthole_cmd_build as build
    src = inspect.getsource(build.cmd_build)
    assert 'args.action or "auto"' in src, src[:200]


def test_the_failure_tail_names_the_cause_not_the_boilerplate():
    """Measured on the first real `porthole pkg` run: the last six lines of a
    failed pmbootstrap build are its version banner and a troubleshooting
    link, and the ERROR line that said what happened was above them. A tail
    that reliably prints boilerplate is the "fail loudly" rule failing
    quietly."""
    log = "\n".join([
        "[11:28:34] => edge/device-x: Building package",
        "[11:29:02] \033[91mERROR:\033[0m Package not found after build: "
        "/pmb/packages/edge/aarch64/device-x-1-r35.apk",
        "See also: <https://postmarketos.org/troubleshooting>",
        "Run 'pmbootstrap log' for details.",
        "Before you report this error, ensure that pmbootstrap is up to date.",
        "Find the latest version here: https://gitlab.postmarketos.org/tags",
        "Your version: 3.11.1",
        "Channel: systemd-edge",
        "systemd: yes (systemd selected in pmbootstrap init)",
    ])
    import porthole_cmd_build as B

    tail = B.failure_tail(log)
    assert any("Package not found after build" in line for line in tail), tail
    assert len(tail) <= 6


def test_a_log_with_no_error_line_still_gets_a_tail():
    """No match must not mean no output -- something is always better than
    silence when a build has just failed."""
    import porthole_cmd_build as B

    tail = B.failure_tail("\n".join(f"line {n}" for n in range(20)))
    assert tail == [f"line {n}" for n in range(14, 20)]


# ------------------------------------------------ failures that mislead ----
#
# Every case here is a real reported failure whose message names something
# other than the cause. Most come from the redfin (Pixel 5) port, where a
# small local model lost a cycle to each in turn.

def _why(text):
    import porthole_cmd_build as B

    return " ".join(B.diagnose(text))


def test_a_python2_gcc_wrapper_is_named_rather_than_the_missing_python():
    assert "gcc-wrapper" in _why(
        "../scripts/gcc-version.sh: line 26: python: not found")


def test_a_floating_point_driver_is_named_rather_than_the_compiler_flag():
    assert "floating point" in _why(
        "error: '-mgeneral-regs-only' is incompatible with the use of "
        "floating-point types")


def test_multiple_definition_points_at_the_kconfig_that_was_turned_off():
    assert "else" in _why("ld: multiple definition of `logbuffer_log'")


def test_a_missing_lz4_is_named_as_a_makedepends_gap():
    assert "makedepends" in _why("/bin/sh: line 0: lz4: not found")


def test_a_vanished_source_file_is_named_as_a_buildroot_collision():
    """Both real spellings. clang puts the filename after the phrase and cc1
    puts it before; matching one order misses half the reports."""
    for text in (
            "cc1: fatal error: ../fs/configfs/file.c: No such file or directory",
            "clang++: error: no such file or directory: '.../TextMetrics.idl'",
            "fatal error: generated/autoconf.h: No such file or directory"):
        assert "buildroot" in _why(text), text


def test_a_failed_umount_is_named_as_the_missing_lax_flag():
    assert "--lax" in _why("ERROR: Failed to umount: /pmb/chroot_native/dev/shm")


def test_an_ordinary_warning_gets_no_diagnosis():
    """A table that fires on everything is the same as no table."""
    assert _why("warning: unused variable 'x' [-Wunused-variable]") == ""


def test_at_most_two_diagnoses_are_offered():
    """A wall of maybes is the same as no help."""
    import porthole_cmd_build as B

    noisy = ("python: not found\nlz4: not found\nmultiple definition of `x'\n"
             "ERROR: Failed to umount: /pmb/chroot_native/dev/shm\n")
    assert len(B.diagnose(noisy)) <= 2


def test_the_host_branch_names_both_cause_and_fix_with_no_pmbootstrap():
    """A Pixel 5 porter had no host pmbootstrap and the kernel build path
    assumed one, failing with an error that named neither cause nor fix."""
    import shutil
    import porthole_cmd_build as build
    from porthole_cli import Bail

    class FakeOut:
        def __call__(self, *a, **k):
            pass

        def paint(self, s, _color):
            return s

    class FakeCtx:
        root = str(ROOT)
        cfg = {}
        out = FakeOut()

    real_which = shutil.which
    shutil.which = lambda name: None if name == "pmbootstrap" else real_which(name)
    try:
        try:
            build._run(FakeCtx(), "tkbuild", 60, host=True)
        except Bail as exc:
            assert "pmbootstrap" in exc.message, exc.message
            assert "sandbox up" in exc.hint or "install pmbootstrap" in exc.hint, exc.hint
        else:
            raise AssertionError("expected Bail with no host pmbootstrap")
    finally:
        shutil.which = real_which


def test_the_bar_repaints_while_the_child_says_nothing():
    """Measured 2026-08-31 on `porthole pkg build phosh`: pmbootstrap put
    nothing on stdout for four minutes, so the terminal froze at
    `[??????] -- build --/s 32s eta --` while pkg-status.json -- fed by the
    same tracker from pmbootstrap's own log.txt -- reached 87%, 4.93/s, eta
    27s. `porthole pkg watch` was live the whole time, which is the tell: the
    tracker was never behind, only the renderer was, because a stdout line was
    the only thing that repainted it."""
    import porthole_cmd_build as build

    class FakeOut:
        def __call__(self, *a, **k):
            pass

        def paint(self, s, _color):
            return s

    class Term:
        """A tty that counts repaints. `\033[2K` is the erase the bar starts
        with, so one per painted frame."""

        def __init__(self):
            self.painted = 0

        def isatty(self):
            return True

        def write(self, text):
            self.painted += text.count("\033[2K")

        def flush(self):
            pass

    tmp = tempfile.mkdtemp(prefix="porthole-beat-")

    class FakeCtx:
        root = ROOT
        cfg = {"PORTHOLE_RUNDIR": tmp}
        out = FakeOut()

    term, real, beat = Term(), sys.stdout, build.BEAT
    build.BEAT = 0.05        # instead of sleeping through a real heartbeat
    sys.stdout = term
    try:
        # Says NOTHING and exits 0: exactly the quiet phase that froze the bar.
        rc = build._stream(FakeCtx(), ["sh", "-c", "sleep 0.6"], None, 60,
                           "pkg:quiet")
    finally:
        sys.stdout, build.BEAT = real, beat
    assert rc == 0, rc
    # One of these is the final erase in _stream's `finally`; before the fix
    # it was the only one.
    assert term.painted >= 3, term.painted


def test_every_module_staging_path_strips_btf():
    """Two paths push a .ko to the device. Both must strip BTF.

    A module built against a different kernel keeps a .BTF section the loader
    refuses, and the fix lived in exactly one of the two paths for the whole
    life of the `mod` verb. This is a registry check, not a spot check: it
    finds the paths rather than being told them, so a THIRD one cannot land
    without either calling the stripper or failing here.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    stagers, missing = [], []
    for path in sorted(list((root / "tools").glob("*.sh"))):
        text = path.read_text(errors="replace")
        # A path that stages a module for the device: it must transfer a .ko.
        # We search for common transfer verbs (scp, rsync, sftp) not bare ssh,
        # which would match scripts that run commands on the device without
        # transferring files (pipe-based pushes like `cat | ssh ... cat >dest`
        # stay outside this boundary, which is accepted as a tradeoff).
        if not re.search(r"\.ko\b", text):
            continue
        if not re.search(r"\b(scp|rsync|sftp)\b", text):
            continue
        stagers.append(path.name)
        if "tk-strip-btf.py" not in text:
            missing.append(path.name)

    assert stagers, "found no module staging path at all -- the probe is wrong"
    assert not missing, (
        "these stage a .ko for the device without stripping BTF: "
        + ", ".join(missing)
        + " -- see brain/traps and tools/tk-strip-btf.py")


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



def test_upgrade_swaps_the_kernel_flavor_in_one_apk_transaction():
    """The half-applied state is the whole reason `upgrade` exists.

    Two kernel aports own /boot/vmlinuz and every dtb, so `apk add` of the new
    flavor while the old one is installed fails on file conflicts -- and
    registers the new package anyway, leaving both installed with the OLD one
    still owning /boot/vmlinuz. The export that follows then packs the old
    kernel and says nothing. A del-then-add pair reintroduces exactly that
    window, so the removal has to ride along in the add as `!<pkg>`.
    """
    body = _ph_function("tkupgrade-kernel")
    assert '"!$incumbent"' in body, (
        "tkupgrade-kernel no longer removes the incumbent inside the add -- a "
        "separate `apk del` reopens the half-applied window")
    assert "apk del" not in body, (
        "tkupgrade-kernel uses a separate `apk del`; the swap must be one "
        "transaction")


def test_upgrade_verifies_ownership_rather_than_apk_exit_code():
    """apk can exit non-zero having half-applied, and zero having applied to
    the wrong package. Ownership of /boot/vmlinuz is the fact that matters."""
    body = _ph_function("tkupgrade-kernel")
    assert "apk info -W /boot/vmlinuz" in body, (
        "tkupgrade-kernel does not check who owns /boot/vmlinuz after the swap")


def test_upgrade_pushes_modules_before_it_flashes():
    """kernel.release changes on a major bump, so /usr/lib/modules/<new> is
    ABSENT on the phone, not stale. Flashing first strands the device with a
    kernel that has no modules and no way to receive any."""
    body = _ph_function("tkupgrade-kernel")
    push, flash = body.find("tkpush-modules"), body.find("tkflash-boot")
    assert push != -1, "tkupgrade-kernel never pushes modules"
    assert flash != -1, "tkupgrade-kernel never flashes"
    assert push < flash, "tkupgrade-kernel flashes before pushing modules"


def test_upgrade_is_reachable_from_the_ladder():
    """A rung an agent cannot find is a rung that does not exist."""
    import porthole_cmd_build as B
    assert "upgrade" in B.ACTIONS
    assert "upgrade" in B.BUILD_ACTIONS
    assert any(rung[0] == "upgrade" for rung in B.LADDER), (
        "upgrade is not in LADDER, so the preview never offers it")


def _ph_function(name):
    """The body of one shell function in ph-build.sh."""
    text = (ROOT / "tools" / "ph-build.sh").read_text()
    start = text.index(f"\n{name}() {{")
    return text[start:text.index("\n}\n", start)]


def test_upgrade_repairs_a_half_applied_target_before_swapping():
    """A failed `apk add` registers the target with no files unpacked. Purging
    the incumbent on top of that deletes /boot/vmlinuz outright, because apk
    believes the target is already installed and unpacks nothing."""
    body = _ph_function("tkupgrade-kernel")
    fix, swap = body.find("apk fix"), body.find("apk add -U -u")
    assert fix != -1, "tkupgrade-kernel never repairs a half-applied target"
    assert fix < swap, "the repair must run BEFORE the swap, not after"


def test_pushing_modules_tolerates_a_release_that_is_not_on_the_phone_yet():
    """On a major bump kernel.release changes, so /lib/modules/<release> does
    not exist on the device. The rotate-aside must not assume it does."""
    body = _ph_function("tkpush-modules")
    assert "[ -d /lib/modules/$kver ] && sudo mv" in body, (
        "tkpush-modules rotates /lib/modules/$kver unconditionally; a major "
        "kernel bump has nothing there to rotate")


def test_mod_refuses_a_tree_that_has_never_been_built():
    """`mod` is incremental and cannot prepare a tree. Without .output/.config
    kbuild advises running menuconfig, which is the wrong fix and hides the
    real precondition."""
    body = _ph_function("tkmod")
    assert '"$_PH_OUT/.config"' in body, (
        "tkmod does not preflight .output/.config; kbuild's menuconfig advice "
        "leaks through instead")
    # envkernel is sourced by `_ph_activate`, not by tkmod itself -- tkmod
    # called `_ph_find_envkernel` directly when this was written. Both
    # positions are asserted to be REAL: `str.find` returns -1 for a name
    # that has moved, and `preflight < -1` is a false comparison that reads
    # exactly like a caught bug. This test spent its whole life after the
    # `__main__` block and had never once run, so nothing noticed.
    build, envk = body.find("$_PH_OUT/.config"), body.find("_ph_activate")
    assert build != -1 and envk != -1, (
        "tkmod no longer preflights or no longer activates envkernel — this "
        "test names the steps by hand and one of them has been renamed")
    assert build < envk, "the preflight must run before envkernel is sourced"


def test_the_tree_announcement_compares_against_the_device():
    """Tree base and device kernel were both printed and never compared. A
    module built from a two-releases-behind tree is refused by MODVERSIONS
    with a message about a symbol, not about the tree."""
    body = _ph_function("_ph_announce_tree")
    assert "uname -r" in body, "the tree announcement never asks what the device runs"
    assert "WARNING" in body, "a mismatched tree is not called out"


def test_pushing_modules_names_the_siblings_left_behind():
    """MODVERSIONS cannot catch a struct-layout split between two modules
    built from one changed header: the struct has no exported symbol, so no
    CRC disagrees. The push is the only place this can be noticed."""
    text = (ROOT / "tools" / "tk-push-module.sh").read_text()
    assert "NOT being pushed" in text, (
        "tk-push-module.sh does not warn about modules built alongside the "
        "ones being pushed")


def test_the_ccache_off_switch_actually_reaches_the_workspace():
    """It did not. PORTHOLE_NO_CCACHE was documented as the way to turn the
    compiler cache off and the env filter dropped it before podman saw it, so
    the switch worked only on the one path where the cache is already off.
    Found by setting it and watching the build arm the cache anyway."""
    import porthole_cmd_build as build
    saved = {k: os.environ.get(k) for k in build.KNOBS_THAT_CROSS}
    try:
        for k in build.KNOBS_THAT_CROSS:
            os.environ.pop(k, None)
        assert "PORTHOLE_NO_CCACHE" not in build._container_cmd("tkbuild", None, {})

        os.environ["PORTHOLE_NO_CCACHE"] = "1"
        argv = build._container_cmd("tkbuild", None, {})
        assert "PORTHOLE_NO_CCACHE" in argv, argv
        # By name, so the value comes from our environment rather than the
        # podman argv -- the same rule the TK_ secrets follow.
        assert "PORTHOLE_NO_CCACHE=1" not in argv, argv
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_no_host_path_setting_crosses_by_accident():
    """The list is names, not a prefix, because the standing rule is that host
    PORTHOLE_* values never reach the workspace -- the host's PORTHOLE_WORKDIR
    names a directory that does not exist in there, and sending it made a real
    session refuse to build."""
    import porthole_cmd_build as build
    for key in build.KNOBS_THAT_CROSS:
        assert not any(w in key for w in ("DIR", "TREE", "PATH", "WORKDIR")), key


def test_a_relative_kernel_tree_resolves_against_the_workdir():
    """PORTHOLE_KERNEL_TREE=linux-ws worked for the workspace build and broke
    --host with `pushd: linux-ws: No such file or directory`, because each path
    interpreted it against whatever pwd it happened to have."""
    import porthole_cmd_build as build
    got = build._tree({"PORTHOLE_WORKDIR": "/w",
                       "PORTHOLE_KERNEL_TREE": "linux-ws"})
    assert str(got) == "/w/linux-ws", got


def test_an_absolute_kernel_tree_is_left_alone():
    import porthole_cmd_build as build
    got = build._tree({"PORTHOLE_WORKDIR": "/w",
                       "PORTHOLE_KERNEL_TREE": "/elsewhere/linux"})
    assert str(got) == "/elsewhere/linux", got


def test_a_relative_tree_with_no_workdir_is_not_guessed_from_the_cwd():
    """Nothing to resolve against is not a licence to use pwd -- that IS the
    bug. Left as given, so the failure names the path the user typed."""
    import porthole_cmd_build as build
    got = build._tree({"PORTHOLE_KERNEL_TREE": "linux-ws"})
    assert str(got) == "linux-ws", got


def test_tree_inside_translates_a_relative_tree():
    """_tree_inside called .resolve(), which resolves against the PROCESS cwd.
    A relative tree therefore translated to whatever directory the CLI was run
    from, and the container was handed a path that does not exist."""
    import porthole_cmd_build as build
    with tempfile.TemporaryDirectory() as tmp:
        (pathlib.Path(tmp) / "linux-ws").mkdir()
        assert build._tree_inside("linux-ws", tmp) == "/work/linux-ws"


def test_the_shell_and_python_agree_on_a_relative_tree():
    """Two implementations of one rule drift unless something compares them,
    and this pair decides WHICH TREE gets flashed."""
    import porthole_cmd_build as build
    script = ROOT / "tools" / "ph-build.sh"
    probe = (
        'PORTHOLE_WORKDIR=/w PORTHOLE_KERNEL_TREE=linux-ws '
        'PORTHOLE_KERNEL_PKG=k PORTHOLE_DEVICE_PKG=d PORTHOLE_FW_PKG=f '
        'PORTHOLE_DTB_FILE=x.dtb '
        f'bash -c \'source "{script}" >/dev/null 2>&1; echo "$_PH_TREE"\''
    )
    out = subprocess.run(["bash", "-c", probe], capture_output=True,
                         text=True).stdout.strip()
    assert out == str(build._tree({"PORTHOLE_WORKDIR": "/w",
                                   "PORTHOLE_KERNEL_TREE": "linux-ws"})), out


# --------------------------------------------------------- tree selection ---
# `porthole build` defaulted to $PORTHOLE_WORKDIR/linux and stopped there. On
# the taimen repo that is a stale topic branch sitting beside the worktree that
# holds the product branch, so a whole session typed PORTHOLE_KERNEL_TREE by
# hand. What makes it more than typing: the refusal that caught it compared
# VERSION tokens (6.18 vs 7.2), so two trees on ONE version and different
# branches would have built the stale one in silence.

def _branches(mapping):
    """(branch_of, lister) for a pretend repo, so the decision is tested
    without a git checkout -- the same reason _classify is pure."""
    return mapping.get, (lambda _base: sorted(mapping))


def test_autoselect_picks_the_one_tree_on_the_product_branch():
    import porthole_cmd_build as build
    branch_of, lister = _branches({"/w/linux": "wifi-disablekey-test",
                                   "/w/linux-ws": "taimen-v7.2"})
    got = build._autoselect_tree("/w", "/w/linux", "taimen-v7.2",
                                 branch_of, lister)
    assert got == "/w/linux-ws", got


def test_autoselect_refuses_when_two_trees_match():
    """Silently choosing between two candidates is how a half-finished branch
    gets flashed. The bar is 'there is exactly one right answer'."""
    import porthole_cmd_build as build
    branch_of, lister = _branches({"/w/linux": "stale", "/w/linux-a": "v7.2",
                                   "/w/linux-b": "v7.2"})
    assert build._autoselect_tree("/w", "/w/linux", "v7.2",
                                  branch_of, lister) == ""


def test_autoselect_refuses_when_no_tree_matches():
    import porthole_cmd_build as build
    branch_of, lister = _branches({"/w/linux": "stale", "/w/linux-ws": "other"})
    assert build._autoselect_tree("/w", "/w/linux", "v7.2",
                                  branch_of, lister) == ""


def test_autoselect_does_nothing_when_the_default_is_already_right():
    import porthole_cmd_build as build
    branch_of, lister = _branches({"/w/linux": "v7.2", "/w/linux-ws": "v7.2"})
    assert build._autoselect_tree("/w", "/w/linux", "v7.2",
                                  branch_of, lister) == ""


def test_autoselect_does_nothing_without_a_product_branch():
    """PORTHOLE_KERNEL_BRANCH is the only thing that says which branch is the
    product. Unset, there is no right answer for a tree to be the only one of."""
    import porthole_cmd_build as build
    branch_of, lister = _branches({"/w/linux": "a", "/w/linux-ws": "b"})
    assert build._autoselect_tree("/w", "/w/linux", "",
                                  branch_of, lister) == ""


def test_autoselect_does_nothing_when_the_default_branch_is_unreadable():
    """A detached or unreadable HEAD is not evidence the default is wrong --
    and inside the workspace container every worktree reads that way, which is
    why this decision is made on the host and not in ph-build.sh."""
    import porthole_cmd_build as build
    branch_of, lister = _branches({"/w/linux": "", "/w/linux-ws": "v7.2"})
    assert build._autoselect_tree("/w", "/w/linux", "v7.2",
                                  branch_of, lister) == ""


def test_autoselect_never_picks_the_default_itself():
    import porthole_cmd_build as build
    branch_of, lister = _branches({"/w/linux": "v7.2"})
    assert build._autoselect_tree("/w", "/w/linux", "v7.2",
                                  branch_of, lister) == ""


def test_the_tree_banner_says_the_tree_is_unused_on_the_flashing_rungs():
    """`fast` announced a tree it then does not use.

    The rung installs and flashes the APORT apk -- the source comments say so
    outright -- so a banner naming a tree reads as "this is what you are
    building" for something that cannot affect the result.
    """
    import porthole_cmd_build as build

    for rung in ("fast", "upgrade"):
        line = build.tree_banner(rung, "/x/linux-ws", "r22")
        assert "not used" in line, (rung, line)
        assert "r22" in line, (rung, line)
    # On the rungs that DO build the tree, it still names the tree.
    line = build.tree_banner("kernel", "/x/linux-ws", "r22")
    assert "linux-ws" in line, line


def test_a_detached_head_reads_as_no_branch():
    """git prints the literal word HEAD for a detached checkout, and treating
    that as a branch name would let it match a branch called HEAD."""
    import porthole_cmd_build as build
    with tempfile.TemporaryDirectory() as tmp:
        assert build._branch_of(tmp) == ""


def test_autoselect_finds_a_real_worktree_on_the_product_branch():
    """The seam above is pure; this proves the wiring. A real repo with a real
    worktree, because the whole defect was that the obvious implementation
    silently never fired against the thing it was meant to find."""
    import porthole_cmd_build as build
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        main = work / "linux"
        main.mkdir()
        git = ["git", "-C", str(main)]
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        quiet = {"capture_output": True, "env": env}
        subprocess.run(["git", "init", "-q", "-b", "stale", str(main)], **quiet)
        (main / "Makefile").write_text("VERSION = 7\n")
        subprocess.run([*git, "add", "-A"], **quiet)
        subprocess.run([*git, "commit", "-qm", "x"], **quiet)
        subprocess.run([*git, "worktree", "add", "-q", "-b", "taimen-v7.2",
                        str(work / "linux-ws")], **quiet)
        got = build._autoselect_tree(str(work), str(main), "taimen-v7.2",
                                     build._branch_of)
        assert got == str(work / "linux-ws"), got
        # And it must NOT fire once the default is on the product branch.
        subprocess.run([*git, "checkout", "-q", "-b", "other"], **quiet)
        subprocess.run([*git, "branch", "-qm", "other", "taimen-v7.2-main"], **quiet)
        got2 = build._autoselect_tree(str(work), str(main), "stale",
                                      build._branch_of)
        assert got2 == "", got2


# ------------------------------------------------------- the routing window ---
# `porthole build` (preview) runs a REAL incremental make, so it built the
# artefact and the run that followed found everything up to date: "make rebuilt
# nothing -- there is nothing to push", about a tree with a real change in it.
# Routing since the last PUSH is durable -- a preview pushes nothing, so it
# cannot move the stamp.

def test_the_push_stamp_is_keyed_on_the_tree():
    """Two trees in one checkout must not share a stamp, or pushing from one
    makes `auto` believe the other reached the device."""
    import porthole_cmd_build as build
    a = build._pushed_stamp("/r/.run", "/w/linux")
    b = build._pushed_stamp("/r/.run", "/w/linux-ws")
    assert a != b, (a, b)
    assert a.name.startswith("pushed-"), a


def test_the_python_and_shell_stamp_names_agree():
    """The stamp is written by ph-build.sh and read in python. Two spellings of
    one name is a stamp that is never found -- and the failure is SILENT,
    because a missing stamp is a legal state meaning 'never pushed'.

    Compared against `cksum` itself rather than a golden number: the point is
    that the two implementations agree, not that either matches a constant
    someone once pasted in."""
    import porthole_cmd_build as build
    for tree in ("/w/linux-ws", "/var/home/x/linux", "/w/a b/linux"):
        want = build._pushed_stamp("/r", tree).name
        got = subprocess.run(
            ["bash", "-c", 'printf %s "$1" | cksum | cut -d" " -f1', "_", tree],
            capture_output=True, text=True).stdout.strip()
        assert want == "pushed-" + got, (tree, want, got)


def test_the_stamp_is_what_the_shell_actually_writes():
    """Not just the name: the file the shell creates must be the file python
    stats. This is the whole seam, and both halves are cheap to run."""
    import porthole_cmd_build as build
    script = ROOT / "tools" / "ph-build.sh"
    with tempfile.TemporaryDirectory() as tmp:
        rundir = pathlib.Path(tmp) / "run"
        tree = "/w/linux-ws"
        probe = (
            f'PORTHOLE_WORKDIR=/w PORTHOLE_KERNEL_TREE={tree} '
            f'PORTHOLE_RUNDIR={rundir} PORTHOLE_KERNEL_PKG=k '
            f'PORTHOLE_DEVICE_PKG=d PORTHOLE_FW_PKG=f PORTHOLE_DTB_FILE=x.dtb '
            f'bash -c \'source "{script}" >/dev/null 2>&1; _ph_pushed_write\''
        )
        subprocess.run(["bash", "-c", probe], capture_output=True)
        assert build._pushed_stamp(rundir, tree).exists(), \
            sorted(p.name for p in rundir.iterdir()) if rundir.exists() else "no rundir"


def test_artefacts_older_than_the_last_push_are_not_work_to_do():
    """The routing question, stated directly: what has make built that the
    device has not received."""
    import porthole_cmd_build as build
    with tempfile.TemporaryDirectory() as tmp:
        tree = pathlib.Path(tmp) / "linux"
        out = tree / ".output" / "arch" / "arm64" / "boot"
        out.mkdir(parents=True)
        dtb = out / "x.dtb"
        dtb.write_text("dtb")
        built = dtb.stat().st_mtime
        assert build._changed_artifacts(tree, built - 1) == \
            ["arch/arm64/boot/x.dtb"]
        assert build._changed_artifacts(tree, built + 1) == []


def test_the_boot_rung_does_not_claim_to_need_no_pmbootstrap():
    """`boot` compiles through _ph_activate -> envkernel -> pmbootstrap. The
    description said "no pmbootstrap", so --host read as the escape hatch when
    the workspace was down and died with `Failed to install all dependencies`
    -- which names neither the cause nor that it could never have worked. It
    means no PACKAGING step."""
    import porthole_cmd_build as build
    _, desc = build.ACTIONS["boot"]
    assert "no pmbootstrap" not in desc, desc
    assert "packaging" in desc, desc


def test_the_ladder_and_the_verb_table_agree_about_boot():
    """AGENTS.md and --help both print from these, and a description that
    drifts from what the rung does is how --host got offered."""
    import porthole_cmd_build as build
    assert not any("no pmbootstrap" in text
                   for row in build.LADDER for text in row), build.LADDER


def test_the_host_branch_says_what_host_building_actually_needs():
    """--host reads as the escape hatch when the workspace is down. Every rung
    compiles through envkernel, so it needs a host pmbootstrap that can BUILD
    -- chroots and dependencies -- and a host that merely has it on PATH fails
    inside pmbootstrap's own dependency install, naming none of that.

    A warning rather than a refusal: a host that can build is legitimate, and
    the exact precondition is not cheaply checkable from here."""
    src = (ROOT / "lib" / "porthole_cmd_build.py").read_text()
    assert "chroots and " in src and "workspace exists to avoid needing" in src


def test_run_follows_pmbootstraps_own_log():
    """The kernel rungs must read log.txt, not only pmbootstrap's stdout.

    pmbootstrap relays `=> step` lines to stdout and writes the actual build
    output -- every CC, every LD -- to its own log.txt. `porthole pkg` has
    always followed it; `porthole build` never did, so build-history.json
    recorded compile_lines 0 for every kernel rung ever run and the bar had
    nothing to move on.

    `_run` bails out before ever reaching `_stream` if `pmbootstrap` is not
    on PATH (see `test_the_host_branch_names_both_cause_and_fix_with_no_pmbootstrap`
    above), and CI has no pmbootstrap installed -- so `shutil.which` is
    stubbed here too, to report it present regardless of the real machine.
    """
    import shutil
    import porthole_cmd_build as build

    seen = {}

    def fake_stream(ctx, cmd, env, timeout, rung, **kw):
        seen.update(kw)
        seen["rung"] = rung
        return 0

    real_stream = build._stream
    real_which = shutil.which
    build._stream = fake_stream
    shutil.which = lambda name: (
        "/usr/bin/pmbootstrap" if name == "pmbootstrap" else real_which(name))
    try:
        ctx = _FakeCtx({"PORTHOLE_DEVICE": "google-taimen",
                        "PORTHOLE_WORKDIR": "/nonexistent"})
        build._run(ctx, "tkbuild-kernel", 60, host=True, rung="fast")
    finally:
        build._stream = real_stream
        shutil.which = real_which

    assert seen.get("follow"), "no follow= was passed"
    assert str(seen["follow"]).endswith("log.txt"), seen["follow"]


def test_a_non_export_rung_keeps_its_bare_rung_as_the_history_key():
    """`mod` and `boot` are not bimodal on the apk-present axis `fast` and
    `upgrade` are (`kernel` always does a full install+export and is
    deliberately not in EXPORT_RUNGS either -- see
    test_kernel_is_not_gated_on_its_own_precondition) -- fix round 1 gated
    the history-bucket split on EXPORT_RUNGS so those two rungs keep matching
    an EXISTING unkeyed history entry (`{"mod": {...}}`) instead of losing it
    to a `mod|cached` / `mod|rebuild` split nothing about them needs. That
    property has no other test pinning it: without the gate this passes with
    `key="mod|cached"` just as easily as with the intended `key="mod"`.
    """
    import shutil
    import porthole_cmd_build as build

    seen = {}

    def fake_stream(ctx, cmd, env, timeout, rung, **kw):
        seen.update(kw)
        seen["rung"] = rung
        return 0

    real_stream = build._stream
    real_which = shutil.which
    build._stream = fake_stream
    shutil.which = lambda name: (
        "/usr/bin/pmbootstrap" if name == "pmbootstrap" else real_which(name))
    try:
        ctx = _FakeCtx({"PORTHOLE_DEVICE": "google-taimen",
                        "PORTHOLE_WORKDIR": "/nonexistent",
                        "PORTHOLE_KERNEL_PKG": "linux-google-taimen"})
        build._run(ctx, "tkmod", 60, host=True, rung="mod")
    finally:
        build._stream = real_stream
        shutil.which = real_which

    assert seen.get("key") == "mod", seen


PMB_LOG_TAIL = """\
(rootfs_google-taimen) install postmarketos-mkinitfs
* mkinitfs: skipping (no deviceinfo file found)
OK: 96.2 MiB in 19 packages
/usr/share/deviceinfo/deviceinfo: "..." not found, required by mkinitfs
/etc/deviceinfo: "..." not found, required by mkinitfs
ERROR: Command failed (exit code 1): (rootfs_google-taimen) % mkinitfs
*** Additional information: log file, examples ***
See also: <https://postmarketos.org/troubleshooting>
"""


def test_failure_tail_finds_the_cause_not_the_boilerplate():
    import porthole_cmd_build as build

    lines = build.failure_tail(PMB_LOG_TAIL)
    joined = "\n".join(lines)
    assert "required by mkinitfs" in joined, joined
    assert "postmarketos.org/troubleshooting" not in "\n".join(lines[:-1]), joined


def test_a_missing_deviceinfo_is_diagnosed_as_an_uninstalled_chroot():
    import porthole_cmd_build as build

    why = build.diagnose(PMB_LOG_TAIL)
    assert why, "no diagnosis for the mkinitfs/deviceinfo signature"
    assert "install" in why[0].lower(), why


def test_a_failure_is_diagnosed_from_this_run_not_an_earlier_one():
    """Regression: `_stream` used to `tail_text(follow)` on the whole of
    pmbootstrap's log.txt, which is SHARED and LONG-LIVED -- it accumulates
    every invocation in this work dir. A user hit this for real: their build
    failed because of stale envkernel `_p` apks, but porthole diagnosed an
    EARLIER, unrelated failure recorded higher up in the same log.txt (the
    deviceinfo/mkinitfs signature above) and told them to run the very
    command they had just run.

    Property under test: an OLD failure signature already in the log before
    this run starts must NOT reach diagnose() -- only what THIS run appends
    after the recorded offset may.
    """
    import porthole_cmd_build as build

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-diag-"))
    follow = tmp / "log.txt"
    # An older, unrelated failure already sitting in the shared log.
    follow.write_text(PMB_LOG_TAIL)

    seen = []

    class FakeOut:
        def __call__(self, text):
            seen.append(text)

        def paint(self, s, _color):
            return s

    class FakeCtx:
        root = ROOT
        cfg = {"PORTHOLE_RUNDIR": str(tmp / "run")}
        out = FakeOut()

    # THIS run's own, different failure -- appended to the SAME file after
    # `_stream` has recorded its start offset, exactly as pmbootstrap keeps
    # appending to its one persistent log.txt.
    new_failure = "line 0: lz4: not found"
    cmd = ["sh", "-c", f"echo '{new_failure}' >> {follow}; exit 1"]

    rc = build._stream(FakeCtx(), cmd, None, 60, "pkg:x", follow=follow)
    assert rc != 0

    text = "\n".join(seen)
    assert "makedepends" in text, text            # this run's real cause
    assert "never had a full" not in text, text    # not the stale earlier one


def test_tail_text_reads_the_end_of_a_large_file():
    import tempfile
    import porthole_cmd_build as build

    path = pathlib.Path(tempfile.mkdtemp(prefix="porthole-tail-")) / "log.txt"
    path.write_text("filler\n" * 200000 + "THE LAST LINE\n")
    got = build.tail_text(path, limit_bytes=4096)
    assert "THE LAST LINE" in got
    assert len(got) <= 5000, len(got)


def test_watch_is_an_action_not_a_flag():
    # A store_true --watch would be a MODE encoded as a boolean, which permits
    # nonsense combinations; tests/test_cli_rules.py forbids that repo-wide,
    # and `status` is already an action for the same reason.
    import porthole_cmd_build as build

    action_arg = [a for a in build.SPEC["args"] if a[0] == ["action"]][0]
    assert "watch" in action_arg[1]["choices"], action_arg[1]["choices"]


def test_detach_argv_forwards_every_flag_that_changes_the_build():
    """A flag dropped here is a declared flag that silently does nothing.

    `pkg` lost --force and --wait this way: `pkg build X --force --detach`
    built without force, and --wait leaking made the parent return OK while
    the child bailed on a busy buildroot. Rebuilt by hand, so every flag has
    to be listed.
    """
    import argparse
    import porthole_cmd_build as build

    # verbose and allow_env_override are True too -- both flags previously
    # went untested (the brief's own fixture set them False), which is
    # exactly the shape of gap that let `pkg` lose --force and --wait
    # unnoticed: a flag can be dropped from detach_argv and a test asserting
    # only the False default would never see it missing.
    args = argparse.Namespace(timeout=5400, kernel=True, host=True,
                              verbose=True, yes=True, rest=[], detach=True,
                              allow_env_override=True)
    argv = build.detach_argv("/x/bin/porthole", "boot", args)

    assert argv[:3] == ["/x/bin/porthole", "build", "boot"], argv
    assert "--yes" in argv, "a detached build that does not build is useless"
    assert "--kernel" in argv, argv
    assert "--host" in argv, argv
    assert "--verbose" in argv, argv
    assert "--allow-env-override" in argv, argv
    assert "--timeout" in argv and "5400" in argv, argv
    # The one flag that must NOT be forwarded, or the child detaches again.
    assert "--detach" not in argv, argv


def test_detach_argv_forwards_the_mod_arguments():
    import argparse
    import porthole_cmd_build as build

    args = argparse.Namespace(timeout=5400, kernel=False, host=False,
                              verbose=False, yes=True, detach=True,
                              allow_env_override=False,
                              rest=["drivers/media/i2c/imx179.ko", "imx179"])
    argv = build.detach_argv("/x/bin/porthole", "mod", args)
    assert argv[-2:] == ["drivers/media/i2c/imx179.ko", "imx179"], argv


if __name__ == "__main__":
    sys.exit(main())
