#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole build` and `porthole flash` -- the loop the toolkit was missing.

The build/flash cycle existed only as `tools/ph-build.sh`, which must be
SOURCED (it defines shell functions and needs envkernel's aliases in the
caller's shell). That made it unreachable from `porthole run`, which executes
tools rather than sourcing them -- so the second half of a port had no verb at
all, and a third device either forked 700 lines of shell or hand-rolled the
envkernel loop from a runbook.

These verbs source it in a subshell and call the function, so the sourced-env
requirement is honoured and the capability becomes addressable: `porthole
build`, `porthole flash`, and therefore also `porthole next`, the TUI, and any
agent reading the verb table.

Flashing is irreversible on the wrong slot, so it goes through the same
confirmation boundary as everything else: `--yes` or nothing happens.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import threading
import time
import sys

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_UNAVAILABLE, EX_USAGE

# Each verb maps to a shell function ph-build.sh defines. The names are kept
# from the taimen toolbox because they are what every runbook prints and what
# people already type interactively.
#
# The ladder, cheapest rung first. Picking the lowest rung that covers your
# change is the single biggest speed lever in this toolbox, and it was
# unreachable: `mod` and `boot` existed only as shell functions nothing in the
# verb table named, so an agent reading `porthole build --help` saw the ~10
# minute rung and used it to iterate on one driver.
#
# `fast` named `tkfast`, which has never existed in ph-build.sh -- the function
# is `tkbuild-kernel`, so the verb failed with "command not found" for every
# caller. tests/test_build_flash.py now asserts every name here is real.
# The descriptions are printed as "would <description>", so they read as verb
# phrases rather than labels.
# `auto` is deliberately NOT in here. ACTIONS maps a rung to a ph-build.sh
# function, and a test asserts every target really exists -- because `fast`
# pointed at a `tkfast` that never existed for the whole life of the verb.
# `auto` has no shell function; it chooses one. Keeping it out leaves that
# invariant absolute instead of adding an exemption a real typo could hide in.
AUTO_DESC = "build the cheapest rung that covers what actually changed"

ACTIONS = {
    "mod": ("tkmod",
            "build one module, push it, reload it and verify -- no reboot (~40s)"),
    # NOT "no pmbootstrap". tkboot calls _ph_activate, which sources
    # envkernel, which IS pmbootstrap -- so the old wording made --host look
    # like a viable escape hatch when the workspace was down, and it never was.
    # It means no PACKAGING step, which is the part that makes it cheap.
    "boot": ("tkboot",
             "build the dtb, repack and RAM-boot it -- no packaging step (~40s)"),
    "fast": ("tkbuild-kernel",
             "build the kernel and flash boot only, UUIDs untouched (~6m)"),
    "kernel": ("tkbuild",
               "build the kernel, package it, install and verify, but NOT flash (~10m)"),
    "upgrade": ("tkupgrade-kernel",
                "swap to a DIFFERENT kernel flavor: push modules, flash boot (~7m)"),
    "clean": ("tkclean", "unstack /mnt/linux binds"),
    "purge": ("tkpurge-devpkgs", "remove envkernel apks that outrank a release"),
}

# The rungs that compile and move the device. `clean` and `purge` are neither.
# `auto` is here so its FALLBACK -- no tree yet, or an unfilled profile --
# renders the ladder preview instead of running an empty function name.
BUILD_ACTIONS = ("auto", "mod", "boot", "fast", "kernel", "upgrade")

# What each rung covers, so the preview can say why you would pick another.
# This is the table an agent needs and had no way to get.
# `boot` is DTS-ONLY by default for a reason. Adding --kernel rebuilds Image.gz,
# and a rebuilt kernel will not load the modules already on the device: the
# build id and the BTF move, so every .ko is refused. That is not a CONFIG-change
# hazard as this table said until 2026-08-27 -- it is EVERY --kernel rebuild.
# On a device whose initramfs needs a module to mount root (taimen loop-mounts
# its subpartition, so it needs loop.ko) the RAM boot cannot reach userspace at
# all: it lands in the initramfs debug shell looking like a bad kernel.
LADDER = [
    ("mod", "a driver that is a module -- try this FIRST, even when the device "
     "ships from an aport: MODVERSIONS makes an ABI mismatch a loud refusal",
     "no reboot at all"),
    ("boot", "a DTS change", "one fastboot boot"),
    ("boot --kernel", "built-in code, IF this device RAM-boots without modules",
     "one fastboot boot"),
    ("fast", "a CONFIG change, or anything that moves module CRCs -- builds and "
     "flashes the APORT release, so the change must be in the series",
     "flashes boot only"),
    ("kernel", "rootfs contents changed, or boot/rootfs desynced",
     "then `porthole flash --yes`"),
    ("upgrade", "the device moves to a DIFFERENT kernel flavor (a major version "
     "bump): PORTHOLE_KERNEL_PKG now names another aport, so kernel.release "
     "changes and the modules on the phone are absent rather than stale",
     "pushes modules, then flashes boot"),
]


def _script(ctx) -> pathlib.Path:
    path = pathlib.Path(ctx.root) / "tools" / "ph-build.sh"
    if not path.is_file():
        raise Bail(f"{path} is missing", EX_FAIL)
    return path


# The rungs that end in `pmbootstrap export`, which builds boot.img out of the
# rootfs chroot and therefore needs one a full `pmbootstrap install` has
# populated. `mod` and `boot` never reach it.
#
# `kernel` is deliberately ABSENT, though tkbuild also calls `pmbootstrap
# export` (ph-build.sh ~825). tkbuild runs `pmbootstrap install` first
# (~821), which is what CREATES and populates the rootfs chroot -- so
# `kernel` is the rung that FIXES an uninstalled chroot, not one that needs
# it already fixed. Gating it here made export_problems() refuse `kernel`
# with "run `porthole build kernel --yes` once against this work dir" --
# its own advice, unrunnable, a circular refusal a real session hit.
EXPORT_RUNGS = ("fast", "upgrade")


def export_problems(workdir, device: str) -> list:
    """Can `pmbootstrap export` run in this work dir? Pure, given a path.

    `fast` is advertised at ~6m and is what the ladder steers you to for a
    config or series change. On a work dir whose rootfs chroot has never been
    installed it CANNOT succeed, and it discovered that only after a full
    kernel compile -- 20m35s, measured. deviceinfo is the file mkinitfs names
    when it fails, so it is the file to test for.
    """
    if not device:
        return []
    chroot = pathlib.Path(workdir) / f"chroot_rootfs_{device}"
    if (chroot / "usr" / "share" / "deviceinfo" / "deviceinfo").is_file():
        return []
    return [f"the rootfs chroot in {workdir} has never been installed, so "
            f"`pmbootstrap export` cannot build a boot.img "
            f"(no {chroot}/usr/share/deviceinfo/deviceinfo). "
            f"Run `porthole build kernel --yes` once against this work dir."]


def _preflight(ctx, action: str = "") -> list[str]:
    """What must be true before a build can even start.

    Reported together rather than one failure at a time: an envkernel build is
    minutes long, and finding out about the second missing value after the
    first one is fixed is how an afternoon goes.

    `action` is optional: the `auto` guard calls this with none, asking
    "could this profile build at all"; the real call site passes the rung it
    is about to run, which is what lets the export-rung check below know
    whether it applies.
    """
    problems = []
    cfg = ctx.cfg
    for key in ("PORTHOLE_WORKDIR", "PORTHOLE_KERNEL_PKG", "PORTHOLE_DEFCONFIG",
                "PORTHOLE_ARCH", "PORTHOLE_DTB"):
        if not cfg.get(key):
            problems.append(f"{key} is not set in the profile")
    workdir = cfg.get("PORTHOLE_WORKDIR", "")
    if workdir and not pathlib.Path(workdir).is_dir():
        problems.append(f"PORTHOLE_WORKDIR does not exist: {workdir}")
    if not shutil.which("pmbootstrap"):
        problems.append("pmbootstrap is not on PATH")
    problems += _space_problems(cfg)
    if action in EXPORT_RUNGS:
        problems += export_problems(
            pmb_workdir(ctx, _workspace_usable(ctx)[0]),
            cfg.get("PORTHOLE_DEVICE", ""))
    return problems


# Measured on the reference host, 2026-08-29, on an established workdir:
#   chroot_native 15G · cache_apk_aarch64 2.0G · rootfs chroot 2.1G · total 29G
# A kernel tree and its objects sit on top of that. These are the headroom a
# build needs to FINISH, not the size of the result -- which is the number that
# matters, because running out at minute forty costs the whole build.
SPACE_FLOOR_GB = 5      # below this a build cannot finish; refuse
SPACE_WARN_GB = 20      # below this it may, and it is worth saying so


def _free_gb(path: str) -> float:
    st = os.statvfs(path)
    return (st.f_bsize * st.f_bavail) / (1024 ** 3)


def _space_problems(cfg) -> list[str]:
    """Refuse a build that cannot finish, in one second rather than forty
    minutes.

    pmbootstrap has its own check, but it runs after the chroots are prepared
    and only covers the image it is about to create. The expensive part is
    everything before that.
    """
    workdir = cfg.get("PORTHOLE_PMB_DIR") or str(
        pathlib.Path.home() / ".local/var/pmbootstrap")
    probe = pathlib.Path(workdir).expanduser()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = _free_gb(str(probe))
    except OSError:
        return []
    if free < SPACE_FLOOR_GB:
        return [f"only {free:.1f} GB free on {probe} -- a build needs at least "
                f"{SPACE_FLOOR_GB} GB to finish. Free some space, or point "
                f"PORTHOLE_PMB_DIR at a bigger filesystem"]
    return []


def _space_warning(cfg) -> str:
    """Not a refusal: tight but survivable, and worth knowing before you wait."""
    workdir = cfg.get("PORTHOLE_PMB_DIR") or str(
        pathlib.Path.home() / ".local/var/pmbootstrap")
    probe = pathlib.Path(workdir).expanduser()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = _free_gb(str(probe))
    except OSError:
        return ""
    if free < SPACE_WARN_GB:
        return (f"{free:.1f} GB free on {probe}. A full kernel rung wants "
                f"more like {SPACE_WARN_GB} GB; this may still work, but "
                f"ENOSPC at minute forty is the usual way it does not")
    return ""


def _assert_no_drift(ctx, args) -> None:
    """Refuse to build something other than what the profile says.

    A stale `export PORTHOLE_KERNEL_PKG=...-6.18` outranks a profile committed
    at 7.2, and nothing said so: the build succeeded and produced the retired
    kernel. Observed twice -- once poisoning a rootfs chroot. `porthole config`
    could always have shown it; nobody runs `porthole config` mid-build.
    """
    import porthole

    drifts = [d for d in porthole.drift(ctx.cfg) if d["blocking"]]
    advisories = [d for d in porthole.drift(ctx.cfg) if not d["blocking"]]
    for adv in advisories:
        ctx.out.warn(f"{adv['key']} is {adv['winning']} from the environment, "
                     f"but the {adv['committed_layer']} says "
                     f"{adv['committed']}")
    if not drifts or getattr(args, "allow_env_override", False):
        return

    detail = "\n  ".join(porthole.drift_lines(drifts))
    raise Bail(
        "the environment is overriding the profile:\n  " + detail,
        EX_FAIL,
        "the profile is committed; your shell is not.\n"
        "          unset " + " ".join(d["key"] for d in drifts)
        + "    use the profile\n"
        "          porthole build --allow-env-override     you mean it")


def _tree(cfg) -> pathlib.Path:
    """Where the kernel tree is, matching ph-build.sh:51 exactly.

    A RELATIVE PORTHOLE_KERNEL_TREE resolves against PORTHOLE_WORKDIR -- the
    device working repo -- and never against the process cwd. The knob is the
    toolbox's own advice ("set PORTHOLE_KERNEL_TREE to build a worktree"), and
    `PORTHOLE_KERNEL_TREE=linux-ws` built fine in the workspace and died on
    --host with `pushd: linux-ws: No such file or directory`: one variable,
    two meanings, decided by whatever directory each path happened to run in.

    With no workdir there is nothing to resolve AGAINST, and falling back to
    the cwd is precisely the bug. Left as given, so the eventual failure names
    the path that was typed rather than one nobody wrote.
    """
    workdir = (cfg.get("PORTHOLE_WORKDIR") or "").strip()
    tree = (cfg.get("PORTHOLE_KERNEL_TREE") or "").strip()
    if tree:
        path = pathlib.Path(tree).expanduser()
        if path.is_absolute() or not workdir:
            return path
        return pathlib.Path(workdir).expanduser() / path
    return pathlib.Path(workdir).expanduser() / "linux" if workdir else pathlib.Path()


def _branch_of(path) -> str:
    """The branch a tree is on, or "" for detached, missing or not-a-repo.

    "" for detached is deliberate and load-bearing: a linked worktree seen from
    inside the workspace container cannot have its branch resolved, and a
    selection that fired on an unresolvable HEAD would be guessing in exactly
    the environment where builds run.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    head = proc.stdout.strip() if proc.returncode == 0 else ""
    return "" if head == "HEAD" else head


def _autoselect_tree(workdir, default, want, branch_of, lister=None) -> str:
    """The one sibling tree on the product branch, or "" to change nothing.

    PURE given `branch_of` and `lister` -- the same reason `_classify` is pure.
    This is a decision that must not be wrong, and a pure function is one that
    can be wrong in a test instead of on a device.

    WHY IT LIVES HERE AND NOT IN ph-build.sh. `porthole build` defaulted to
    $PORTHOLE_WORKDIR/linux and stopped there, so on a repo whose product
    branch lives in a sibling worktree every invocation of a whole session
    needed PORTHOLE_KERNEL_TREE typed by hand. The refusal that caught it only
    fired because the VERSION tokens differed (6.18 vs 7.2); two trees on one
    version and different branches would have built the stale one in silence,
    which is the hazard the branch-divergence rule exists for.

    The obvious place for the fix is ph-build.sh, and that is the wrong place:
    git inside the workspace container cannot resolve a linked worktree's
    branch, so a shell implementation would silently never fire in the one
    environment where builds actually run. Up here git runs on the HOST, and
    the answer reaches the container through the PORTHOLE_KERNEL_TREE
    translation _container_cmd already performs.

    Deliberately narrow. Switching trees at all is only safe when there is
    exactly one right answer -- silently choosing between two candidates is how
    a half-finished branch gets flashed. Every ambiguous case (no product
    branch named, an unreadable default HEAD, zero matches, two matches) keeps
    today's behaviour and leaves the existing version-token refusal to speak.
    """
    if not workdir or not want:
        return ""
    # An unreadable default HEAD is not evidence the default is WRONG, and it
    # is also what stops this firing on a plain non-git directory.
    here = branch_of(default)
    if not here or here == want:
        return ""
    if lister is None:
        def lister(base):
            return sorted(str(p) for p in pathlib.Path(base).glob("linux*")
                          if p.is_dir())
    try:
        candidates = lister(workdir)
    except OSError:
        return ""
    matches = [c for c in candidates if c != default and branch_of(c) == want]
    return matches[0] if len(matches) == 1 else ""


def _classify(changed) -> tuple:
    """Which rung covers what an incremental make actually rebuilt.

    PURE -- a list of changed artifact paths in, (rung, args, reason) out --
    because this is the decision that must not be wrong, and a pure function is
    one that can be wrong in a test instead of on a device.

    Measured rather than inferred from the source diff, and the difference is
    not academic: a header edit moves every module's CRC without looking like a
    config change, and a Kconfig edit can flip a module to built-in. Both fool
    a diff reader. Neither fools "what did make actually write".
    """
    kos = sorted(p for p in changed if p.endswith(".ko"))
    image = [p for p in changed
             if p.endswith(("/Image.gz", "/Image", "Image.gz", "Image"))]
    dtbs = [p for p in changed if p.endswith(".dtb")]

    if not changed:
        return (None, [], "make rebuilt nothing -- there is nothing to push")

    if image:
        return ("fast", [],
                "Image.gz moved, so every module's CRC may have moved with it; "
                "a pushed module would be refused by the running kernel")

    if kos and not dtbs:
        if len(kos) == 1:
            name = pathlib.Path(kos[0]).name[:-len(".ko")]
            return ("mod", [kos[0], name],
                    f"one module rebuilt ({name}) and nothing else")
        # brain/traps/pushing-one-module-of-a-pair-corrupts-the-other.md: modules
        # built together share a struct layout, and pushing a subset corrupts
        # the ones left behind. So several is not several `mod` runs.
        return ("fast", [],
                f"{len(kos)} modules rebuilt together; pushing a subset "
                f"corrupts the ones left behind")

    if dtbs and not kos:
        return ("boot", [], "only the dtb changed")

    return ("fast", [],
            "both the dtb and modules changed, and no cheap rung covers both")


_CKSUM_TABLE = None


def _cksum(data: bytes) -> str:
    """POSIX cksum, so python and `cksum` in ph-build.sh name the same file.

    NOT zlib.crc32: cksum uses the CRC-32/CKSUM variant -- unreflected, initial
    value 0 -- and then feeds the LENGTH through the same register. Getting
    that subtly wrong would produce a stamp the shell writes and python never
    finds, and the failure would be SILENT, because a missing stamp is a legal
    state meaning "never pushed". tests/test_build_flash.py compares the two
    implementations against each other rather than against a golden value.
    """
    global _CKSUM_TABLE
    if _CKSUM_TABLE is None:
        table = []
        for i in range(256):
            crc = i << 24
            for _ in range(8):
                crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
                crc &= 0xFFFFFFFF
            table.append(crc)
        _CKSUM_TABLE = table
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CKSUM_TABLE[((crc >> 24) ^ byte) & 0xFF]
    length = len(data)
    while length:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CKSUM_TABLE[
            ((crc >> 24) ^ (length & 0xFF)) & 0xFF]
        length >>= 8
    return str((~crc) & 0xFFFFFFFF)


def _pushed_stamp(rundir, tree) -> pathlib.Path:
    """Where the last-push time for THIS tree is recorded.

    Keyed per tree: two trees in one checkout must not share a stamp, or
    pushing from one makes `auto` believe the other reached the device.
    Written by ph-build.sh's _ph_pushed_write, read here.
    """
    return pathlib.Path(rundir) / f"pushed-{_cksum(str(tree).encode())}"


def _changed_artifacts(tree: pathlib.Path, since) -> list:
    """Artifacts under .output newer than `since`, as tree-relative paths."""
    out = tree / ".output"
    if not out.is_dir():
        return []
    found = []
    for path in out.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".ko", ".dtb") and path.name not in (
                "Image.gz", "Image"):
            continue
        try:
            if path.stat().st_mtime > since:
                found.append(str(path.relative_to(out)))
        except OSError:
            continue
    return sorted(found)


def _workspace_usable(ctx):
    """(usable, why_not) -- is the workspace wired for THIS device?

    A merely RUNNING container is not enough, and assuming it was cost a real
    session three confused build attempts: the container had been created for a
    different device, so the build routed into it and died with "could not read
    pkgver/pkgrel / aport is ." -- an error with no relationship to the actual
    problem. The only clue was a grey line saying "in the workspace".

    So the container has to agree with us about which phone this is. It already
    records that as a label at creation time, for the device-mutex guard; this
    reads the same label. Never raises: deciding WHERE to build must not be a
    way for the build verb to break.
    """
    try:
        import porthole_cmd_sandbox as sandbox
    except Exception:  # noqa: BLE001
        return False, "the sandbox module is unavailable"
    if not shutil.which("podman"):
        return False, "podman is not installed"
    try:
        if not sandbox._container_running():
            return False, "no workspace is running"
        want = sandbox._lock_path(ctx.cfg.get("PORTHOLE_DEVICE", ""))
        got = sandbox._container_lock()
        if got and got != want:
            return False, (f"the running workspace is wired for {got}, not "
                           f"{want} -- `porthole sandbox down` then `up`")
        if not got:
            return False, ("the running workspace predates the device label, "
                           "so it cannot be matched to this device -- "
                           "`porthole sandbox down` then `up`")
    except Exception:  # noqa: BLE001
        return False, "could not query the workspace"
    return True, ""


def apk_is_current(packages_dir, pkgname: str, pkgver: str, pkgrel: str,
                   arch: str) -> bool:
    """Is the release apk this rung will install already built? Pure.

    The same question _ph_install_kernel_release asks in shell before deciding
    whether to build the aport. Asking it in Python BEFORE the run starts is
    what lets the ETA know which of two very different builds this is.
    """
    apk = pathlib.Path(packages_dir) / arch / \
        f"{pkgname}-{pkgver}-r{pkgrel}.apk"
    return apk.is_file()


def aport_version(ctx):
    """`(pkgver, pkgrel)` for the configured kernel aport, or ("", "").

    One parser, reused: porthole_cmd_pkg.apkbuild_version already reads an
    APKBUILD and a second reader of the same file is a second thing to be
    wrong about `pkgrel`.
    """
    import porthole_cmd_aports as aports
    import porthole_cmd_pkg as pkg

    name = ctx.cfg.get("PORTHOLE_KERNEL_PKG", "")
    if not name:
        return "", ""
    try:
        directory = aports._pkg_dir(aports._pmaports(ctx), name)
    except Exception:  # noqa: BLE001 -- no pmaports is not a build failure
        return "", ""
    if directory is None:
        return "", ""
    version = pkg.apkbuild_version(directory)      # "7.2.2-r22"
    pkgver, _, pkgrel = version.partition("-r")
    return pkgver, pkgrel


def _release_apk_present(ctx, usable: bool) -> bool:
    """Is the kernel apk this rung will install already built?

    The same question _ph_install_kernel_release asks in shell before it
    decides whether to build the aport. Asked here, BEFORE the run starts, it
    is what lets the ETA know which of two very different builds this is.

    `usable` is `_run`'s own workspace-vs-host decision, passed in rather than
    re-derived. A second call to `_workspace_usable` here disagreed with the
    first whenever `--host` forced a host build with a workspace still up: the
    build ran on the host while this unforced re-query still read the
    WORKSPACE's packages/edge, silently reintroducing the very
    averaging-two-populations bug this module exists to remove, for exactly
    those builds, and printing a message that could contradict where the
    build was about to run. Threading the value through cannot disagree with
    it by construction.

    Unknown counts as "present": an ETA that under-promises is a pleasant
    surprise, and refusing to guess is already what `eta unknown` is for.
    """
    pkgver, pkgrel = aport_version(ctx)
    if not pkgver or not pkgrel:
        return True
    return apk_is_current(
        pmb_workdir(ctx, usable) / "packages" / "edge",
        ctx.cfg.get("PORTHOLE_KERNEL_PKG", ""), pkgver, pkgrel,
        ctx.cfg.get("PORTHOLE_ARCH") or "aarch64")


def pmb_workdir(ctx, in_container: bool) -> pathlib.Path:
    """pmbootstrap's own work dir, on the HOST filesystem either way.

    Lives here rather than in `pkg` because the decision it depends on --
    _workspace_usable -- lives here, and because BOTH build paths need it now:
    the kernel rungs to follow log.txt, and the export rungs to check whether
    the rootfs chroot has ever been installed.

    The workspace deliberately keeps its own pmbootstrap work dir, separate
    from the host's. That split is the whole reason `fast` can find both apks
    and still fail in export: the two dirs have different chroots.
    """
    import porthole_cmd_sandbox as sandbox

    if in_container:
        return sandbox._sandbox_pmb(ctx.cfg)
    host = ctx.cfg.get("PORTHOLE_PMB_DIR") or "~/.local/var/pmbootstrap"
    return pathlib.Path(host).expanduser()


def _tree_inside(tree, workdir) -> str:
    """PORTHOLE_KERNEL_TREE as the CONTAINER sees it, or "" if it cannot.

    A worktree is the toolbox's own advice -- ph-build prints "set
    PORTHOLE_KERNEL_TREE to build a worktree" on every run -- and the knob is a
    host path, so it stops at the container boundary like every other one
    (docs/SANDBOX-PROVISIONING.md §4b). The difference is that this one CAN be
    translated: a worktree kept inside the device repo is mounted, at /work.

    A tree outside the device repo is not reachable inside at all, so this
    returns "" and the caller leaves the variable unset -- the container then
    builds its default tree, which is wrong but visibly so, rather than dying
    on a path that does not exist.
    """
    if not tree or not workdir:
        return ""
    try:
        base = pathlib.Path(workdir).expanduser()
        path = pathlib.Path(tree).expanduser()
        # Same rule as _tree, and it has to be the same rule: .resolve() alone
        # resolves a relative value against the PROCESS cwd, so a worktree
        # named relatively translated to whatever directory the CLI was run
        # from and the container was handed a path that does not exist.
        if not path.is_absolute():
            path = base / path
        rel = path.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        return ""
    return "/work" if str(rel) == "." else f"/work/{rel}"


# Behaviour switches that must reach the workspace. Deliberately a list of
# NAMES, not a prefix: see _container_cmd, where the standing rule is that
# host PORTHOLE_* values never cross because they name host paths.
KNOBS_THAT_CROSS = ("PORTHOLE_NO_CCACHE", "PORTHOLE_LAX_BUILD")


def _container_cmd(func: str, extra: list[str] | None,
                   secrets, tree_inside: str = "") -> list[str]:
    """The podman exec line for a build, as argv.

    Pure, so where a build runs is testable without podman, a device or a
    workdir -- none of which CI has.

    The container's own PORTHOLE_* values were set when it was created and are
    correct for the paths INSIDE it, so they are deliberately not re-sent from
    the host, where PORTHOLE_WORKDIR names a directory that does not exist in
    here. Only TK_* runtime values cross, because those are things the user set
    for this invocation -- a password among them.
    """
    import porthole_cmd_sandbox as sandbox

    call = " ".join([func, *(shlex.quote(a) for a in extra or [])])
    argv = ["podman", "exec"]
    # TK_ only, enforced HERE rather than trusting the caller to filter: the
    # rule is that host paths never cross, and a rule that lives in the caller
    # is one a second caller will not have. PORTHOLE_WORKDIR is the one that
    # bites -- the host's names a directory that does not exist inside, and
    # sending it is what made a real session refuse to build.
    #
    # `-e NAME`, not `-e NAME=value`: podman takes the value from OUR
    # environment, so a rootfs password never appears in the podman argv where
    # `ps` would show it to every user on the box. TK_PMOS_PASSWORD is exactly
    # such a value, and tkbuild requires it.
    for key in sorted(k for k in secrets if k.startswith("TK_")):
        argv += ["-e", key]
    # The narrow exception, by NAME and not by prefix. These two are switches
    # rather than settings: `1` or unset, no path in either, so none of them
    # can name a directory that does not exist in here. Everything the rule
    # above exists to stop is a value; a list of names cannot grow into one by
    # accident.
    #
    # Found by running it. PORTHOLE_NO_CCACHE was documented as the way to turn
    # the compiler cache off and did nothing in the workspace, because the
    # filter dropped it before podman ever saw it -- so the off switch worked
    # only on the one path (`--host`) where the cache is off anyway.
    # PORTHOLE_LAX_BUILD had the same hole, unnoticed because the advice is not
    # to use it.
    for key in KNOBS_THAT_CROSS:
        if os.environ.get(key):
            argv += ["-e", key]
    # The one host path that is translated rather than dropped: see
    # _tree_inside. Sent as NAME=value, not NAME -- the value is the
    # container's path, not ours, so it cannot come from our environment.
    if tree_inside:
        argv += ["-e", f"PORTHOLE_KERNEL_TREE={tree_inside}"]
    argv += [sandbox.CONTAINER, "/bin/bash", "-lc",
             f"cd /porthole && source tools/ph-build.sh && {call}"]
    return argv


def _host_cmd(script: pathlib.Path, func: str,
              extra: list[str] | None) -> list[str]:
    """bash, not sh: ph-build.sh uses arrays, `shopt -s expand_aliases` and
    `pushd`, and envkernel's `make` is an alias only bash expands."""
    call = " ".join([func, *(shlex.quote(a) for a in extra or [])])
    return ["bash", "-c", f'source "{script}" && {call}']


def build_env(cfg, base) -> dict:
    """The environment a build child gets. Pure, so what crosses is testable.

    PMB_SUDO is REMOVED. doctor already fails on it and calls it a leftover
    whose privilege broker is gone -- but an export survives in a shell long
    after the file does, and pmbootstrap invokes it directly, so a stale one
    kills the build with exit 78 deep inside pmbootstrap with nothing anywhere
    saying the words PMB_SUDO. porthole builds already refuse to inherit
    config drift; this is drift by another name.
    """
    env = dict(base)
    env.pop("PMB_SUDO", None)
    for key, value in cfg.items():
        if key.startswith(("PORTHOLE_", "TK_")) and isinstance(value, str):
            env[key] = value
    return env


def _run(ctx, func: str, timeout: int, extra: list[str] | None = None,
         host: bool = False, rung: str = "") -> int:
    """Run one of ph-build.sh's functions, in the workspace or on the host."""
    script = _script(ctx)
    env = build_env(ctx.cfg, os.environ)

    # shlex.quote, not naive interpolation: these arguments are a path and a
    # module name that reach a shell, and a path with a space in it would
    # otherwise arrive as two arguments.
    call = " ".join([func, *(shlex.quote(a) for a in extra or [])])
    usable, why_not = (False, "--host") if host else _workspace_usable(ctx)
    if usable:
        secrets = {k: v for k, v in env.items()
                   if k.startswith("TK_") and isinstance(v, str)}
        cmd = _container_cmd(func, extra, secrets,
                             _tree_inside(env.get("PORTHOLE_KERNEL_TREE"),
                                          env.get("PORTHOLE_WORKDIR")))
        # Not grey. WHERE a build ran is the first thing you need when it fails
        # in a way that makes no sense, and burying it cost someone three
        # attempts before they noticed the tail.
        ctx.out(ctx.out.paint("  building IN THE WORKSPACE (container)", "cyan"))
    else:
        # A Pixel 5 porter had no workspace and no host pmbootstrap either,
        # and hit an error that named neither cause nor fix.
        if not shutil.which("pmbootstrap"):
            # 69, not 1: nothing ran, so this is not "the build failed".
            raise Bail(
                f"no workspace and no host pmbootstrap ({why_not})",
                EX_UNAVAILABLE,
                "run `porthole sandbox up`, or install pmbootstrap on the "
                "host — the redfin port hit this with neither and got an "
                "error that named neither")
        cmd = _host_cmd(script, func, extra)
        ctx.out(ctx.out.paint(f"  building ON THE HOST ({why_not})", "cyan"))
        # --host reads as the escape hatch when the workspace is down, and it
        # is a weaker one than it looks. EVERY rung compiles through envkernel
        # -- `boot` included, whatever its description used to say -- so this
        # needs a host pmbootstrap that can actually build: configured work
        # dir, chroots, dependencies. A host that has pmbootstrap on PATH but
        # has never built with it fails inside pmbootstrap's own dependency
        # install, which names none of that. Not a refusal, because a host
        # that CAN build is a legitimate setup; said out loud because the
        # failure that follows will not say it.
        if host:
            ctx.out(ctx.out.paint(
                "  --host needs a host pmbootstrap that can build (chroots and "
                "dependencies),", "grey"))
            ctx.out(ctx.out.paint(
                "  which is what the workspace exists to avoid needing", "grey"))
    # WHERE a build ran is the first thing you need when it fails in a way
    # that makes no sense, and WHICH WORK DIR is the second. The
    # workspace/host split is documented and was invisible at build time,
    # which is how `fast` came to find both apks present and still fail in
    # export against a chroot that had never been installed.
    ctx.out(ctx.out.paint(
        f"  work dir: {pmb_workdir(ctx, usable)}"
        f"  ({'workspace' if usable else 'host'})", "grey"))
    # pmbootstrap keeps the real build output in its own log.txt and puts only
    # `=> step` lines on stdout. `pkg` has followed it since the bar was
    # written; the kernel rungs never did, so build-history.json recorded
    # `compile_lines: 0` for every kernel build ever run and `fast` rendered
    # [??????] for the 20 minutes that dominate it.
    effective_rung = rung or func

    # `fast` (and every export rung) is two very different builds wearing one
    # name: install + export + flash when the release apk already exists, and
    # a full compile plus package plus that same install/export/flash when it
    # does not -- measured at 6m43s against 21m24s. porthole knows which one
    # this is before it starts, the same way _ph_install_kernel_release does
    # in shell, so say it rather than let a "~6m" advertisement stand while a
    # 21-minute compile runs silently underneath it.
    #
    # Gated on EXPORT_RUNGS -- the same table that already answers "is this
    # rung bimodal on this axis" for `_preflight`'s chroot check. `mod` and
    # `boot` are not: the apk-present lookup would cost them a pmaports glob
    # and an APKBUILD read on every run for nothing, and splitting their
    # history key would halve the sample pool of the two rungs an agent hits
    # hardest, doubling how often THEY report "eta unknown". Keeping their key
    # as the bare rung name has a pleasant side effect too: an existing
    # unkeyed `{"mod": {...}}` entry keeps matching, because only the rungs
    # that were actually being averaged wrongly lose their old history.
    key = effective_rung
    if effective_rung in EXPORT_RUNGS:
        rebuilding = not _release_apk_present(ctx, usable)
        if rebuilding:
            ctx.out(ctx.out.paint(
                f"  the {effective_rung} rung must build the kernel package "
                f"first — this is the full-compile path, not the "
                f"install-and-flash one", "yellow"))
        else:
            ctx.out(ctx.out.paint(
                "  the kernel package is already built — install, export "
                "and flash only", "grey"))

        import porthole_progress as progress

        key = progress.history_key(effective_rung, rebuilding)

    return _stream(ctx, cmd, env, timeout, effective_rung, key=key,
                   follow=pmb_workdir(ctx, usable) / "log.txt")


# How often the bar repaints and the status file is written while the child
# says nothing. Named rather than inline so the test can turn it down instead
# of sleeping through a real one.
BEAT = 1.0


def _stream(ctx, cmd, env, timeout: int, rung: str,
            tracker_cls=None, log_prefix: str = "build", follow=None,
            on_kill=None, key: str = "") -> int:
    """Run the build, publishing where it is the whole time.

    Every line goes to a log file unconditionally, so "quiet by default" never
    costs anyone the output they needed. The terminal gets a live bar when it
    is a terminal, a periodic line when it is not (an agent's pipe), and the
    raw stream under --verbose.

    `follow` is a second line source, tailed in the background. pmbootstrap
    needs it: it prints only its own `=> step` lines to stdout and sends the
    ACTUAL build output -- abuild, meson, every ninja `[N/M]` -- to its own
    log.txt. Parsing only stdout meant a 991-step ninja build produced a bar
    that never moved, and no amount of unbuffering fixes that, because the
    lines were never written to stdout in the first place.

    `tracker_cls` and `log_prefix` are what let `porthole pkg` share this. The
    subprocess plumbing -- the log, the timeout kill, the tail on failure, the
    15-second heartbeat for a pipe -- is identical for a package and a kernel;
    only the thing reading the lines differs. Two copies of this loop is how
    the package path would have ended up with no log rotation and no tail.
    """
    import porthole_progress as progress

    # log.txt is shared and long-lived -- pmbootstrap appends to the SAME file
    # across every invocation in this work dir, forever. A build that failed
    # here once got diagnosed with advice about a *different*, EARLIER
    # failure ("the chroot has never had a full install... run this command"
    # -- for the command the user had just run) because the failure path
    # tailed the whole file instead of only what THIS run appended. Recording
    # the offset before the child starts, and reading only from there, is
    # what keeps diagnose() looking at this run's output.
    follow_offset = 0
    if follow:
        try:
            follow_offset = follow.stat().st_size
        except OSError:
            follow_offset = 0

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    rundir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # `pkg:webkit2gtk-6.0` is the rung; the log wants the name, not the
    # namespace, or it comes out as `pkg-pkg:webkit...` with a colon in the
    # filename that half the shell has to quote.
    slug = rung.split(":", 1)[-1].replace("/", "-")
    logpath = rundir / f"{log_prefix}-{slug}-{stamp}.log"
    verbose = getattr(getattr(ctx, "args", None), "verbose", False)
    tty = sys.stdout.isatty()

    # `key`, when given, is the history bucket (see progress.history_key) --
    # a rung whose cost is bimodal needs to learn each case separately, and
    # the rung itself stays what a reader (snapshot's own "rung" field) sees.
    tracker = (tracker_cls or progress.Tracker)(rundir, rung, key=key)
    tracker.publish(force=True)
    ctx.out(ctx.out.paint(f"  log: {logpath}", "grey"))

    try:
        proc = subprocess.Popen(cmd, env=env, cwd=str(ctx.root),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
    except FileNotFoundError:
        raise Bail("bash is not installed", EX_FAIL) from None

    # Neither the publish nor the BAR below fires unless a LINE arrives on the
    # child's stdout. Quiet phases are normal and long -- installing build
    # dependencies, a cmake configure that prints nothing for ninety seconds --
    # and pmbootstrap is quiet on stdout for most of a build by design, because
    # it sends the real output to the log.txt the `follow` thread reads. A
    # frozen `elapsed` is indistinguishable from a hung build, which is the
    # whole failure this instrumentation exists to end. So the heartbeat drives
    # BOTH renderers, not just the file.
    #
    # publish() is atomic (tmp file + rename), so a heartbeat racing the line
    # loop can only ever leave one whole snapshot behind.
    stop_beat = threading.Event()
    painting = threading.Lock()
    last_note = 0.0

    def _paint():
        """The one-line bar, repainted from the line loop AND the heartbeat.

        Measured on a real `pkg build phosh` 2026-08-31: the terminal sat at
        `[??????] -- build --/s 32s eta --` for four minutes while
        pkg-status.json -- same tracker, written by the heartbeat -- said 87%,
        4.93/s, eta 27s. `porthole pkg watch` in another terminal was live the
        whole time. The bar was not stale because the tracker was behind; it
        was stale because only a stdout line could repaint it, and pmbootstrap
        had not written one since 32s.
        """
        nonlocal last_note
        if verbose:
            return  # --verbose is the raw stream; a bar would fight it
        with painting:
            if stop_beat.is_set():
                return  # the finally below cleared the line -- leave it clear
            if tty:
                sys.stdout.write("\r\033[2K  " + tracker.line())
                sys.stdout.flush()
            elif time.time() - last_note > 15:
                # Not a terminal: an agent's pipe, or CI. A repainting bar
                # would be thousands of useless lines, and silence is the
                # black box this exists to end. One line every 15s is both
                # readable and enough to see it is alive.
                last_note = time.time()
                print("  " + tracker.line(), flush=True)

    def _heartbeat():
        while not stop_beat.wait(BEAT):
            tracker.publish(force=True)
            _paint()

    threading.Thread(target=_heartbeat, daemon=True).start()

    def _follow(path):
        # Seek to the END first: log.txt is shared and long-lived, and
        # replaying somebody else's build from the top would report their
        # progress as ours.
        try:
            with open(path, errors="replace") as handle:
                handle.seek(0, os.SEEK_END)
                while not stop_beat.is_set():
                    line = handle.readline()
                    if line:
                        tracker.feed(line)
                    else:
                        time.sleep(0.2)
        except OSError:
            pass  # no log to follow is not a reason to fail a build

    if follow:
        threading.Thread(target=_follow, args=(follow,), daemon=True).start()

    killed = False
    try:
        # Line buffered: this is the path printed on screen as `log: ...`, and
        # with the default 8 KiB buffer it was still zero bytes four minutes
        # into the phosh build above -- `tail -f` on the file we told them to
        # look at showed nothing.
        with open(logpath, "w", buffering=1) as log:
            for line in proc.stdout:
                log.write(line)
                tracker.feed(line)
                tracker.publish()
                if verbose:
                    sys.stdout.write(line)
                _paint()
                if tracker.elapsed > timeout:
                    killed = True
                    proc.kill()
                    # Killing a `podman exec` client does NOT kill what it
                    # exec'd. Without this the timed-out build keeps compiling
                    # inside the container, still holding the buildroot, and
                    # deletes the source tree of whatever starts next.
                    if on_kill:
                        on_kill()
                    break
    finally:
        stop_beat.set()
        if tty and not verbose:
            with painting:
                sys.stdout.write("\r\033[2K")
                sys.stdout.flush()
        rc = proc.wait()
        tracker.finish(rc == 0 and not killed)

    if killed:
        raise Bail(f"{rung} timed out after {timeout}s", EX_FAIL,
                   f"the partial log is at {logpath}")
    ctx.out(ctx.out.paint(
        f"  {rung}: {progress.fmt_dur(tracker.elapsed)}"
        f"  ({tracker.compile_seen} build steps)", "grey"))
    # A failed build otherwise says only "0 compile steps" and exits 1: the
    # reason is in the log, and whoever is reading this -- an agent especially
    # -- has no idea a log is worth opening. The last few lines are where the
    # shell says why it refused, every time.
    if rc != 0 and not verbose:
        text = logpath.read_text(errors="replace")
        for line in failure_tail(text):
            ctx.out(ctx.out.paint(f"  | {line}", "grey"))
        # pmbootstrap's stdout is reliably NOT where pmbootstrap puts the
        # cause. A 21-minute run died with a tail containing an APKINDEX
        # warning and a line about systemd, while the real error sat 71,393
        # lines into log.txt. failure_tail already prefers the lines that name
        # a failure; it was pointed at the wrong file.
        inner = ""
        if follow:
            import porthole_cmd_pkg as pkg
            try:
                cur_size = follow.stat().st_size
            except OSError:
                cur_size = 0
            if cur_size < follow_offset:
                # Rotated or truncated mid-build: the recorded offset now
                # points past the end, and seeking there would read nothing.
                # Fall back to a plain tail rather than report no cause at
                # all.
                inner = tail_text(follow)
            else:
                inner = pkg.log_since(follow, follow_offset)
        if inner:
            named = failure_tail(inner)
            if named:
                ctx.out(ctx.out.paint(f"  from {follow}:", "grey"))
                for line in named:
                    ctx.out(ctx.out.paint(f"  | {line}", "grey"))
        # The error text is the one thing every porter has in hand, and for a
        # known signature the cause is somewhere the message does not mention.
        for why in diagnose(text + "\n" + inner):
            ctx.out(ctx.out.paint(f"  ? {why}", "yellow"))
    return rc


# Anything that names the failure. Deliberately narrow -- a pattern that
# matched "error" anywhere would select compiler warnings mentioning the word.
_SAYS_WHY = re.compile(
    r"^\s*(ERROR|FATAL|Traceback|error:|fatal:|\S+: error:)|"
    r"\bcould not\b|\bnot found\b|\bNo such file\b", re.I)


def failure_tail(text: str, limit: int = 6) -> list[str]:
    """The lines that say WHY, not merely the last lines.

    The last six lines of a failed `pmbootstrap build` are its version banner
    and a link to the troubleshooting page -- measured, on the first real run
    of `porthole pkg`. The line that mattered, `ERROR: Package not found after
    build: .../device-google-taimen-1-r35.apk`, was six lines above that and
    scrolled off. A tail that reliably prints the boilerplate instead of the
    error is the "fail loudly" requirement failing quietly.

    So: prefer lines that name a failure, keep the last one for context, and
    fall back to a plain tail when nothing matches.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    named = [ln for ln in lines if _SAYS_WHY.search(_strip_ansi(ln))]
    if not named:
        return lines[-limit:]
    keep = named[-(limit - 1):]
    if lines[-1] not in keep:
        keep = keep + [lines[-1]]
    return keep


def tail_text(path, limit_bytes: int = 2_000_000) -> str:
    """The last `limit_bytes` of a file, decoded leniently.

    pmbootstrap's log.txt is shared, long-lived and was 71,393 lines deep when
    it held the answer to a failed build. Reading it whole to print six lines
    is not the shape of a thing that runs at the end of every failure.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


# Build failures whose message names something other than the cause. Each one
# here cost a real session, most of them reported from the redfin (Pixel 5)
# port where a small local model lost a cycle to each in turn. The pattern is
# always the same: the error accuses the compiler, the distro or the kernel,
# and the fix is somewhere else entirely.
#
# This is a lookup table, not cleverness. It earns its place because the
# alternative is every porter rediscovering the same seven answers, and
# because the error text is the ONE thing they all have in hand.
_DIAGNOSES = (
    (re.compile(r"python: not found|gcc-wrapper\.py|gcc-version\.sh", re.I),
     "this kernel wraps CC with scripts/gcc-wrapper.py, which is Python 2 and "
     "will not run on a modern toolchain. Patch the wrapper out "
     "(`CC = $(CROSS_COMPILE)gcc`) and put the patch in source=, not only in "
     "patches=. Common on 4.19-era downstream kernels."),
    (re.compile(r"'-mgeneral-regs-only' is incompatible.*floating-point", re.I),
     "an arm64 kernel build forbids floating point, and this driver uses it. "
     "Disable that driver's CONFIG symbol -- but read the headers first: "
     "disabling a whole subsystem often exposes non-static stubs in the #else "
     "branch and turns this into `multiple definition of`."),
    (re.compile(r"multiple definition of", re.I),
     "a Kconfig symbol was probably turned off whose headers define global "
     "(non-static) stubs in their #else branch, so several objects now carry "
     "the same definition. Re-enable the core symbol and disable only the "
     "leaf driver you actually meant to drop."),
    (re.compile(r"\blz4: not found|\bImage\.lz4\b", re.I),
     "the kernel config compresses with LZ4 and the buildroot has no lz4 "
     "binary. Add `lz4` to makedepends in the kernel APKBUILD."),
    (re.compile(r"is required for USE_[A-Z0-9_]+", re.I),
     "a dependency this build needs was probably appended inside a case/esac. "
     "pmbootstrap parses APKBUILDs line by line and never runs the shell, so "
     "it never installed it. List the package statically as well."),
    (re.compile(r"Failed to umount|umount:.*not mounted", re.I),
     "a non-lax pmbootstrap build cannot run in the rootless workspace: "
     "zap_buildroots() cannot umount the recursive /dev bind. `porthole pkg "
     "build` passes --lax for you; a hand-rolled pmbootstrap call does not."),
    # Either order: clang says "no such file or directory: 'x.idl'" and cc1
    # says "../fs/configfs/file.c: No such file or directory". Matching only
    # one of them misses half the real reports.
    (re.compile(r"No such file or directory[^\n]*\.(?:c|h|S|idl|cpp|cc)\b"
                r"|\.(?:c|h|S|idl|cpp|cc)\b[^\n]*No such file or directory"
                r"|generated/autoconf\.h: No such file", re.I),
     "a source file vanished MID-BUILD, which usually means a second "
     "pmbootstrap command shared this buildroot and wiped $srcdir -- abuild "
     "cleans it before unpacking. Check what is staged: "
     "`ls <workdir>/chroot_buildroot_<arch>/home/pmos/build/src/`. "
     "`porthole pkg build` and `porthole aports` take a lock; raw pmbootstrap "
     "does not."),
    (re.compile(r"deviceinfo[^\n]*not found, required by mkinitfs"
                r"|mkinitfs: skipping \(no deviceinfo file found\)", re.I),
     "`pmbootstrap export` builds boot.img from the ROOTFS CHROOT, and the "
     "chroot in this work dir has never had a full `pmbootstrap install` -- "
     "so it has no device package and no deviceinfo. The workspace keeps its "
     "own pmbootstrap work dir, separate from the host's, so an install done "
     "on the host does not populate it. Run `porthole build kernel --yes` "
     "once against this work dir."),
)


def diagnose(text: str) -> list[str]:
    """Known causes for a failure whose message names something else.

    Returns at most two: the point is to shorten the search, and a wall of
    maybes is the same as no help. Ordered by the table, which is ordered by
    how specific the signature is.
    """
    stripped = _strip_ansi(text)
    hits = [why for pattern, why in _DIAGNOSES if pattern.search(stripped)]
    return hits[:2]


_ANSI = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """pmbootstrap colours the word ERROR, and the escape sits between the
    line start and the word -- so an anchored match fails on exactly the
    lines it most needs to catch."""
    return _ANSI.sub("", text)


def _rung_args(args, action: str) -> list[str]:
    """Validate and shape the trailing arguments a rung takes.

    Checked here rather than in the shell because `tkmod` with one argument
    builds every module in the tree and then fails on a path it cannot resolve
    -- minutes spent to learn about a typo.
    """
    rest = list(getattr(args, "rest", None) or [])
    if action == "mod":
        if len(rest) != 2:
            raise Bail("mod needs the module's path and its name", EX_USAGE,
                       "porthole build mod drivers/media/i2c/imx179.ko imx179 --yes")
        return rest
    if rest:
        raise Bail(f"{action} takes no extra arguments", EX_USAGE,
                   "only `mod` takes arguments (MODULE.ko NAME)")
    if action == "boot" and getattr(args, "kernel", False):
        return ["--kernel"]
    if getattr(args, "kernel", False):
        raise Bail(f"--kernel applies to `boot`, not `{action}`", EX_USAGE)
    return []


def _auto(ctx, args) -> int:
    """Make first, then run the cheapest rung that covers what changed.

    The old default was `kernel` -- the MOST expensive of five rungs, at ~10
    minutes against `mod`'s ~40 seconds. An agent typing the obvious
    `porthole build --yes` got the 15x path, which is most of why sessions were
    spending ten minutes on changes a module push covered.

    Note this compiles even without --yes. That is a deliberate departure from
    the other rungs' preview: the whole question `auto` answers is "which rung",
    and only make can answer it. It touches NO device without --yes.
    """
    import time

    tree = _tree(ctx.cfg)
    if not (tree / "Makefile").is_file():
        raise Bail(f"no kernel tree at {tree}", EX_FAIL,
                   "set PORTHOLE_KERNEL_TREE, or PORTHOLE_WORKDIR with linux/ "
                   "inside it")

    # Local aports drift on their own schedule, and `auto` is where people
    # come to be told what needs rebuilding. It ADVISES and never acts: a
    # webkit build is hours, and a rung chooser that silently started one is
    # the worst surprise this tool could hand anyone. Said BEFORE the make, so
    # it still reaches you when the kernel half fails.
    #
    # Never fatal. Deciding which kernel rung to run must not become breakable
    # by a missing pmaports checkout or an unreadable APKBUILD.
    try:
        import porthole_cmd_pkg as pkg

        usable, _ = _workspace_usable(ctx)
        stale = pkg.outdated(pkg._find_pmaports(ctx),
                             pkg._packages_dir(ctx, usable),
                             ctx.cfg.get("PORTHOLE_ARCH") or "aarch64")
    except Exception:  # noqa: BLE001
        stale = []
    if stale:
        names = ", ".join(name for name, _ in stale[:4])
        more = "" if len(stale) <= 4 else f" (+{len(stale) - 4} more)"
        ctx.out(ctx.out.paint(
            f"  {len(stale)} local aport(s) also need rebuilding: {names}{more}",
            "yellow"))
        ctx.out(ctx.out.paint(
            "  porthole pkg outdated   # why, and what to run", "grey"))

    # WHAT `auto` ROUTES ON. Not "what this make invocation touched": the
    # preview runs a real incremental make, so observing the evidence consumed
    # it. `porthole build` then `porthole build auto --yes` measured a tree
    # that the preview had already brought up to date and concluded there was
    # nothing to do -- so an agent following the documented preview-then-run
    # flow got a build that refused to act, and the natural next move is to
    # type a rung by hand, which AGENTS.md warns against.
    #
    # Since the last PUSH instead. That is durable: a preview compiles but
    # pushes nothing, so it cannot move the stamp, so preview and run agree and
    # running `auto` twice is safe.
    #
    # No stamp -- the first build in a tree -- falls back to the old window. A
    # missing stamp means "never pushed", and treating every artefact under
    # .output as unpushed would route every first build to the most expensive
    # rung: a worse default than the one being replaced.
    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    try:
        since = _pushed_stamp(rundir, tree).stat().st_mtime
        ctx.out(ctx.out.paint(
            "  routing on what make built since the last push ("
            + time.strftime("%H:%M", time.localtime(since)) + ")", "grey"))
    except OSError:
        # A second early, because make writes files as it runs and a clock that
        # ticks between here and the first write would hide the first object.
        since = time.time() - 1
        ctx.out(ctx.out.paint(
            "  no push recorded for this tree yet -- routing on what this "
            "build rebuilds", "grey"))
    ctx.out(ctx.out.paint("  measuring: incremental make, then routing on what "
                          "it actually rebuilt", "grey"))
    # _ph_measure, not _ph_make: the only question here is what make touched,
    # and _changed_artifacts below answers it out of .output. _ph_make would
    # also package -- 14.66 s, and a `_p` apk left in the local repo that
    # outranks every release build. A preview must not do that.
    rc = _run(ctx, "_ph_measure", args.timeout, None,
              host=getattr(args, "host", False), rung="auto")
    if rc != 0:
        return rc

    changed = _changed_artifacts(tree, since)
    rung, extra, reason = _classify(changed)

    ctx.out.blank()
    ctx.out.heading("what make rebuilt")
    for path in changed[:8]:
        ctx.out(f"  {path}")
    if len(changed) > 8:
        ctx.out(f"  ... and {len(changed) - 8} more")
    if not changed:
        ctx.out(ctx.out.paint("  nothing", "grey"))
    ctx.out.blank()

    if rung is None:
        ctx.out(reason)
        return EX_OK

    func, what = ACTIONS[rung]
    call = " ".join(["porthole", "build", rung, *extra, "--yes"])
    ctx.out.heading(f"cheapest rung that covers it: {rung}")
    ctx.out(f"  {reason}")
    ctx.out(f"  {what}")
    ctx.out(ctx.out.paint(f"  {call}", "cyan"))
    ctx.out.blank()

    if not args.yes:
        ctx.out(ctx.out.paint(
            "  nothing was pushed or flashed -- add --yes to run that rung",
            "grey"))
        return EX_OK
    return _run(ctx, func, args.timeout, extra,
                host=getattr(args, "host", False), rung=rung)


def _status(ctx) -> int:
    """Where the running build is. This is what an agent polls INSTEAD of
    sleeping -- brain/laws/poll-never-sleep.md said not to sleep, and until
    now could not say what to read."""
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    path = rundir / "build-status.json"
    try:
        snap = json.loads(path.read_text())
    except (OSError, ValueError):
        return ctx.emit({"state": "none"},
                        lambda: ctx.out("no build has run in this checkout"))

    # The bar and the ETA are drawn by status_report ONLY for a run that is
    # still alive. This used to render them straight out of the snapshot, so a
    # build that died at 11:45 kept printing `[>   ] starting ... eta 6s` for
    # hours: `state failed` was in the output and nobody read it, because a bar
    # says "in flight" louder than a word says otherwise.
    def render():
        head, rows = progress.status_report(snap)
        ctx.out("  " + head)
        for label, value in rows:
            ctx.out.kv(label, value, 10)

    return ctx.emit(snap, render)


def detach_argv(porthole, action: str, args) -> list:
    """The argv a detached build re-invokes itself with. Pure.

    Rebuilt by hand, so every flag that changes what the build DOES must be
    listed here or it is silently dropped -- `pkg` lost --force and --wait
    exactly that way. `--detach` is deliberately absent: forwarding it would
    make the child detach again and orphan the run.
    """
    argv = [str(porthole), "build", action, "--timeout", str(args.timeout)]
    if getattr(args, "kernel", False):
        argv.append("--kernel")
    if getattr(args, "host", False):
        argv.append("--host")
    if getattr(args, "verbose", False):
        argv.append("--verbose")
    if getattr(args, "allow_env_override", False):
        argv.append("--allow-env-override")
    if getattr(args, "yes", False):
        argv.append("--yes")
    return argv + list(getattr(args, "rest", None) or [])


def _detach(ctx, args, action: str) -> int:
    """Start the build in its own session and return immediately.

    Re-invokes this same verb rather than duplicating the run path, so a
    detached build is byte-for-byte the foreground one: same tracker, same
    status file, same log, same artifact check.

    Available on the flashing rungs too. The irreversible-action boundary is
    `--yes`, which was given at launch; detaching does not make a confirmed
    flash less confirmed, and `fast` and `upgrade` are the two rungs an agent
    most needs to detach.
    """
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    rundir.mkdir(parents=True, exist_ok=True)
    spawn_log = rundir / f"build-{action}-detached.log"
    argv = detach_argv(ctx.root / "bin" / "porthole", action, args)
    with open(spawn_log, "w") as handle:
        proc = subprocess.Popen(argv, cwd=str(ctx.root), stdout=handle,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                start_new_session=True)
    # Claim the status file for THIS run before returning, or a `watch` in the
    # next second reads the PREVIOUS build's final snapshot and reports it.
    progress.publish_pending(rundir, action, proc.pid, "build-status.json")
    ctx.out.kv("pid", str(proc.pid), 10)
    ctx.out.kv("log", str(spawn_log), 10)
    ctx.out(ctx.out.paint("  porthole build watch          # live, exits with "
                          "the build", "cyan"))
    ctx.out(ctx.out.paint("  porthole build watch --json   # one JSON object "
                          "per update, for an agent", "cyan"))
    return EX_OK


def _watch(ctx, args) -> int:
    """Follow the kernel build's status file until it stops."""
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))

    # A raw sink, not `ctx.out`: `progress.watch` bakes its own line ending
    # into every string it emits (a bare `\r\033[2K` prefix for a tty
    # redraw-in-place, a trailing `\n` otherwise), matching `pkg`'s `_watch`.
    def out(line):
        sys.stdout.write(line)
        sys.stdout.flush()

    return progress.watch(rundir, "build-status.json",
                          getattr(args, "interval", 1.0), out,
                          ndjson=getattr(args, "json", False),
                          start_hint="start one with `porthole build <action> "
                                     "--yes` or `--detach`")


def tree_banner(rung: str, tree, release: str) -> str:
    """What to say about the tree, for THIS rung. Pure.

    `fast` and `upgrade` install and flash the APORT apk -- the rung's own
    source comments say the tree cannot affect what lands on the phone -- and
    both printed a banner naming a tree immediately before doing so. Keyed on
    the rung and not on whether the tree matches, because the tree is inert on
    these rungs either way.
    """
    if rung in ("fast", "upgrade"):
        return ("flashing the APORT release {} — the tree is not used by this "
                "rung".format(release or "(unknown release)"))
    return "building {}".format(tree)


def _maybe_autoselect_tree(ctx, action: str = "") -> None:
    """Point the build at the tree holding the product branch, if exactly one
    does. Runs before anything reads the tree, and says so loudly when it
    fires -- a build that silently switched trees would be worse than the bug.
    """
    if (ctx.cfg.get("PORTHOLE_KERNEL_TREE") or "").strip():
        return
    want = (ctx.cfg.get("PORTHOLE_KERNEL_BRANCH") or "").strip()
    chosen = _autoselect_tree(
        (ctx.cfg.get("PORTHOLE_WORKDIR") or "").strip(),
        str(_tree(ctx.cfg)), want, _branch_of)
    if not chosen:
        return
    ctx.cfg["PORTHOLE_KERNEL_TREE"] = chosen
    # os.environ too: _run copies ctx.cfg into the child environment, but
    # _tree_inside reads the environment it was handed, and a value in only
    # one of the two is how the host and the container end up building
    # different trees.
    os.environ["PORTHOLE_KERNEL_TREE"] = chosen
    _, pkgrel = aport_version(ctx)
    release = f"r{pkgrel}" if pkgrel else ""
    ctx.out(ctx.out.paint(
        f"  tree: the default is not on the product branch {want}", "yellow"))
    ctx.out(ctx.out.paint(
        f"        {tree_banner(action, chosen, release)}", "yellow"))


def cmd_build(args, ctx) -> int:
    action = args.action or "auto"
    # `status` and `auto` are actions, not flags. A store_true `--status` would
    # be a MODE encoded as a boolean, which permits nonsense combinations and
    # is what tests/test_cli_rules.py forbids repo-wide.
    if action == "status":
        return _status(ctx)
    if action == "watch":
        return _watch(ctx, args)
    if action != "auto" and action not in ACTIONS:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: auto, status, watch, {', '.join(ACTIONS)}")

    _assert_no_drift(ctx, args)
    _maybe_autoselect_tree(ctx, action)

    if action == "auto":
        # Measuring needs a tree to make in. Without one -- a new port, or a
        # profile nobody has filled in yet -- fall through to the ordinary
        # preview rather than erroring: "a profile that cannot build yet is the
        # normal state of a new port", and a preview SHOWS what is wrong
        # instead of refusing to describe it.
        if not _preflight(ctx) and (_tree(ctx.cfg) / "Makefile").is_file():
            return _auto(ctx, args)
        func, what = "", AUTO_DESC
        extra = []
    else:
        func, what = ACTIONS[action]
        extra = _rung_args(args, action)

    problems = _preflight(ctx, action)
    tight = _space_warning(ctx.cfg)
    if tight and not problems:
        ctx.out.warn(tight)

    # A preview SHOWS what is wrong; it does not refuse. Refusing to describe
    # the build because the build could not run is unhelpful precisely when you
    # most need to know why -- and a profile that cannot build yet is the
    # normal state of a new port.
    if not args.yes and action in BUILD_ACTIONS:
        def render():
            o = ctx.out
            o.heading(f"would {what}")
            o.kv("device", ctx.cfg.get("PORTHOLE_DEVICE", ""), 12)
            o.kv("tree", ctx.cfg.get("PORTHOLE_WORKDIR", "")
                 or o.paint("not set", "yellow"), 12)
            o.kv("package", ctx.cfg.get("PORTHOLE_KERNEL_PKG", "")
                 or o.paint("not set", "yellow"), 12)
            o.kv("defconfig", ctx.cfg.get("PORTHOLE_DEFCONFIG", "")
                 or o.paint("not set", "yellow"), 12)
            o.blank()
            if problems:
                o.heading(f"{len(problems)} thing(s) missing first")
                for problem in problems:
                    o(f"  {o.paint(o.sym('·', '-'), 'yellow')} {problem}")
                o.blank()
            # The ladder, every time. The expensive mistake here is not a bad
            # build, it is iterating on the ~10 minute rung when the ~40 second
            # one covers the change -- and nothing used to say the cheap rungs
            # existed.
            o.heading("pick the cheapest rung that covers your change")
            for name, covers, cost in LADDER:
                mark = o.sym(">", "*") if name == action else " "
                o(f"  {mark} {o.paint(name.ljust(7), 'cyan')} {covers}"
                  f"  {o.paint('(' + cost + ')', 'grey')}")
            o.blank()
            if not problems:
                o.hint(f"porthole build {' '.join([action, *extra])} --yes")
        return ctx.emit({"action": action, "function": func, "args": extra,
                         "would_run": not problems, "problems": problems,
                         "ladder": [dict(zip(("rung", "covers", "cost"), r))
                                    for r in LADDER]},
                        render)

    if problems and action in BUILD_ACTIONS:
        raise Bail("this profile cannot build yet", EX_FAIL,
                   "; ".join(problems))

    if not func:
        # `auto` with --yes but nothing to measure with. Say which of the two
        # it is rather than running an empty command.
        raise Bail("cannot choose a rung: there is no kernel tree to measure",
                   EX_FAIL,
                   f"expected {_tree(ctx.cfg)}/Makefile -- set "
                   f"PORTHOLE_KERNEL_TREE, or name an explicit rung")

    # After the `--yes` gate, so a detached build is still a confirmed one --
    # `--detach` changes WHERE the build runs, not whether it was confirmed.
    if getattr(args, "detach", False):
        return _detach(ctx, args, action)

    rc = _run(ctx, func, args.timeout, extra,
              host=getattr(args, "host", False), rung=action)
    if rc != 0:
        raise Bail(f"{func} failed", EX_FAIL,
                   "the output above is the build's; `pmbootstrap log` has more")
    ctx.out(ctx.out.paint(f"  {what}: done", "green"))
    return EX_OK


SPEC = {
    "verb": "build",
    "order": 36,
    "help": "build the kernel and package it, through envkernel",
    "description": (
        "The envkernel loop, as a verb. It was only ever a shell file you had\n"
        "to SOURCE, which meant `porthole run` could not reach it and the\n"
        "second half of a port had no command at all.\n\n"
        "Two traps are encoded in the script it drives, both paid for in real\n"
        "sessions: `pmbootstrap build --envkernel` can write an apk and then\n"
        "fail before refreshing the index, and a stale _p snapshot outranks a\n"
        "release build. Artifacts are verified rather than exit codes trusted."),
    "escapes_scope": True,
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["auto", "status", "watch"] + list(ACTIONS),
                      "help": "auto | status | watch | " + " | ".join(ACTIONS)
                              + "  (default: auto)"}),
        (["rest"], {"nargs": "*", "metavar": "ARG",
                    "help": "mod: MODULE.ko NAME"}),
        (["--kernel"], {"action": "store_true",
                        "help": "boot: rebuild Image.gz too, not just dtbs"}),
        (["--timeout"], {"type": int, "default": 5400, "metavar": "SEC",
                         "help": "seconds before giving up (default 5400)"}),
        (["--allow-env-override"], {"action": "store_true",
                                    "help": "build what the environment says, "
                                            "not what the profile says"}),
        (["--verbose"], {"action": "store_true",
                         "help": "stream the raw build output instead of a "
                                 "progress line"}),
        (["--host"], {"action": "store_true",
                      "help": "build on the host, not in the workspace"}),
        (["--yes"], {"action": "store_true", "help": "actually build"}),
        (["--detach"], {"action": "store_true",
                        "help": "start the build in its own session and "
                                "return; follow it with `build watch`"}),
        (["--interval"], {"type": float, "default": 1.0, "metavar": "SEC",
                          "help": "watch: seconds between reads (default 1)"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_build,
    "examples": [
        "porthole build",
        "porthole build mod drivers/media/i2c/imx179.ko imx179 --yes",
        "porthole build boot --yes",
        "porthole build boot --kernel --yes",
        "porthole build fast --yes",
        "porthole build fast --yes --detach",
        "porthole build watch",
        "porthole build watch --json",
        "porthole build kernel --yes",
        "porthole build clean",
    ],
}
