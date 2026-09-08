#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Which build sites exist on THIS host, and which one an operation gets.

Split from porthole_plan deliberately: the manifest is pure and must stay
testable with no podman and no work dir, and this half cannot be. Keeping
them in one file would make the manifest untestable, which is the same
mistake as letting a rule live where nothing can run it.
"""
from __future__ import annotations

import os
import pathlib
import shutil

import porthole_plan as plan

# The node's mere existence is the test. Inside the container we are uid 0 by
# mapping, so a permission check there would answer about a uid that owns
# nothing -- and the loop ioctls want CAP_SYS_ADMIN in the namespace that OWNS
# the device, which a user namespace never is.
LOOP_CONTROL = "/dev/loop-control"


def loop_exists() -> bool:
    return pathlib.Path(LOOP_CONTROL).exists()


def can_make_image(site: str, loop_exists: bool) -> bool:
    """Pure. The sandbox can never make one, whatever the host has."""
    if site == plan.SANDBOX:
        return False
    return bool(loop_exists)


def choose_from(op, available, prefer=None, host_can_image=True):
    """(site, why_not) for an Op, given the sites that exist here. Pure.

    Returns (None, reason) when nothing can run it, so the caller refuses with
    a sentence rather than an inference.

    A `prefer` that cannot run the operation is REFUSED, not quietly swapped:
    silently honouring the fallback is how builds came to run against a
    different package repo than the one the reader was looking at.

    `host_can_image` is the host half of the question `can_make_image`
    answers -- computed by the caller (`can_make_image(HOST, loop_exists())`)
    so this stays pure. The static `op.sites` table says `HOST: True`
    unconditionally for an op that produces a rootfs image, because whether
    THIS host has a loop device is not something the manifest can know. Left
    unchecked, a host with no /dev/loop-control would be told it can build
    one and then fail exactly the way the sandbox already refuses for --
    unproven for the host half of the plane's central claim.
    """
    sites_map = op.sites
    if not host_can_image and "rootfs.img" in op.produces:
        sites_map = dict(op.sites)
        sites_map[plan.HOST] = plan.NO_LOOP
    if prefer is not None:
        if prefer not in available:
            return None, f"{prefer} is not available on this host"
        reason = sites_map.get(prefer)
        if reason is not True:
            return None, reason
        return prefer, ""
    # Sandbox first: it needs no standing root, which is the whole point.
    for site in (plan.SANDBOX, plan.HOST):
        if site in available and sites_map.get(site) is True:
            return site, ""
    reasons = [sites_map[s] for s in available if sites_map.get(s) is not True]
    return None, (reasons[0] if reasons else
                  "no build site is available -- `porthole sandbox up`, or "
                  "install pmbootstrap on the host")


def available(ctx) -> list:
    """Which sites this host actually has. Impure."""
    out = []
    if usable(ctx)[0]:
        out.append(plan.SANDBOX)
    if shutil.which("pmbootstrap"):
        out.append(plan.HOST)
    return out


def usable(ctx):
    """(usable, why_not) -- is the workspace wired for THIS device?

    A merely RUNNING container is not enough, and assuming it was cost a real
    session three confused build attempts: the container had been created for
    a different device, so the build routed into it and died with "could not
    read pkgver/pkgrel / aport is ." -- an error with no relationship to the
    actual problem. The only clue was a grey line saying "in the workspace".

    So the container has to agree with us about which phone this is. It
    already records that as a label at creation time, for the device-mutex
    guard; this reads the same label. Never raises: deciding WHERE to build
    must not be a way for the build verb to break.

    Moved verbatim from porthole_cmd_build._workspace_usable -- it was
    correct, only in the wrong place. porthole_cmd_build keeps a thin alias
    of the same name so its many existing callers keep working; a later task
    points them at this copy directly and deletes the alias.
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
        # A container is created ONCE, with the mounts its config named then.
        # Setting a working repo afterwards -- which is the ordinary order on
        # a new host, and what `porthole init` now does -- leaves the running
        # workspace with no /work, and ph-build.sh then dies on its own
        # `${PORTHOLE_WORKDIR:?}` naming neither the container nor the fix.
        stale = sandbox.workdir_drift(ctx.cfg, sandbox._container_mounts())
        if stale:
            return False, stale
    except Exception:  # noqa: BLE001
        return False, "could not query the workspace"
    return True, ""


def facts(ctx, state: str = "") -> dict:
    """The mapping porthole_plan.unmet consumes. Impure; one place.

    `state` is passed in rather than probed, so a preview can render without
    touching the device -- the preview being gated behind device state is one
    of the defects this plane exists to remove.
    """
    # Function-local and DELIBERATE, not an oversight to "tidy" into a
    # module-level import: porthole_cmd_build._preflight will import
    # porthole_sites (a later task), and this module already imports
    # porthole_cmd_build here. Two module-level imports of each other would
    # deadlock the interpreter on whichever loads second; Python only
    # resolves the cycle because both sides delay the import until the
    # function actually runs, by which time both modules already exist in
    # sys.modules. Lifting either one to the top breaks `import porthole_cli`
    # at startup.
    import porthole_cmd_build as build   # for _tree and pmb_workdir

    cfg = ctx.cfg
    workdir = cfg.get("PORTHOLE_WORKDIR", "")
    tree = build._tree(cfg)
    named = str(tree) not in ("", ".")
    site_usable = usable(ctx)[0]
    try:
        st = os.statvfs(str(build.pmb_workdir(ctx, site_usable)))
        free = (st.f_bsize * st.f_bavail) / (1024 ** 3)
    except OSError:
        free = None
    return {
        "state": state,
        plan.TREE: named and (tree / "Makefile").is_file(),
        plan.KERNEL_PKG: cfg.get("PORTHOLE_KERNEL_PKG", ""),
        plan.DEFCONFIG: cfg.get("PORTHOLE_DEFCONFIG", ""),
        plan.ARCH: cfg.get("PORTHOLE_ARCH", ""),
        plan.DTB: cfg.get("PORTHOLE_DTB", ""),
        plan.WORKDIR: workdir and pathlib.Path(workdir).is_dir(),
        plan.ROOTFS_PW: build.rootfs_password(cfg),
        plan.CHROOT_INSTALLED: not build.export_problems(
            build.pmb_workdir(ctx, site_usable),
            cfg.get("PORTHOLE_DEVICE", "")),
        "free_gb": free,
    }
