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


if __name__ == "__main__":
    sys.exit(main())


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
    build, envk = body.find("$_PH_OUT/.config"), body.find("_ph_find_envkernel")
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
