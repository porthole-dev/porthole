#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What every operation needs, where it can run, and what it costs.

WHY ONE MANIFEST
    This knowledge existed in nine places that could not see one another, and
    the cost was measured: `porthole flash --yes` chose a site, printed
    "building IN THE WORKSPACE", and died at the last step because that site
    cannot create a rootfs image. Nothing had asked. The nine places were
    _preflight, export_problems, _workspace_usable, _ph_can_make_image,
    ph_need_fastboot, LADDER, TREE_RUNGS, EXPORT_RUNGS and INSTALL_RUNGS --
    three of which are needs-tables for the same rungs.

WHY IT IS PURE
    A manifest that has to probe a host is one you cannot test without one,
    and the decision it drives -- refuse or proceed -- is exactly the decision
    that must never be wrong. Facts come in as a mapping (see
    porthole_sites.facts); this module never runs a subprocess.

WHY `sites` HOLDS A REASON AND NOT A BOOLEAN
    "the sandbox cannot do this" sends the reader looking. The reason IS the
    answer, and it is the same argument the permissions table makes for having
    a deny half: not-in-the-list is an accident and reads exactly like a
    decision.
"""
from __future__ import annotations

# Device state an operation needs before it can start. The same vocabulary the
# tool contract has used since the toolbox existed (`needs: BOOTED`), finally
# available to a verb.
NONE, ANY, BOOTED, FASTBOOT = "NONE", "ANY", "BOOTED", "FASTBOOT"

SANDBOX, HOST = "sandbox", "host"

# Inputs an operation needs, as keys into the facts mapping. Named here so a
# typo is a KeyError in a test rather than a silently-skipped check.
TREE = "tree"                    # a kernel tree with a Makefile
KERNEL_PKG = "kernel_pkg"        # PORTHOLE_KERNEL_PKG
DEFCONFIG = "defconfig"          # PORTHOLE_DEFCONFIG
ARCH = "arch"
DTB = "dtb"
WORKDIR = "workdir"
ROOTFS_PW = "rootfs_pw"          # TK_PMOS_PASSWORD, for pmbootstrap install
CHROOT_INSTALLED = "chroot_installed"   # a rootfs chroot `export` can pack

# Why a site cannot run something. Stated once; several ops share it.
NO_LOOP = ("pmbootstrap partitions the rootfs image through a loop device, "
           "and a rootless user namespace cannot have one -- /dev/loop-control "
           "is root:disk on the host and absent in the container")


class Op:
    """One operation: what it needs, where it runs, what it costs and destroys.

    `sites` maps a site to True (it can run there) or to a string saying why
    not. `disk_gb` is the headroom the operation needs to FINISH, which is the
    number that matters -- running out at minute forty costs the whole build.
    """

    def __init__(self, name, summary, needs_state=ANY, needs=(), sites=None,
                 produces=(), destroys=(), reversible=True, disk_gb=5.0):
        self.name = name
        self.summary = summary
        self.needs_state = needs_state
        self.needs = tuple(needs)
        self.sites = dict(sites or {SANDBOX: True, HOST: True})
        self.produces = tuple(produces)
        self.destroys = tuple(destroys)
        self.reversible = reversible
        self.disk_gb = disk_gb

    def runs_in(self, site) -> bool:
        return self.sites.get(site) is True

    def __repr__(self):
        return f"<Op {self.name}>"


_KERNEL_INPUTS = (TREE, KERNEL_PKG, DEFCONFIG, ARCH, DTB, WORKDIR)

OPS = {}


def _add(op):
    OPS[op.name] = op
    return op


# --------------------------------------------------------------- the rungs --

_add(Op("mod",
        "build one module, push it, reload it and verify -- no reboot",
        needs_state=BOOTED, needs=_KERNEL_INPUTS,
        produces=("module",), reversible=True, disk_gb=5.0))

# Same shape as `fast`/`upgrade`, not `flash-boot`: before it compiles
# anything, tkboot ssh's in (`tk_run 'uname -r'`) to seed the base image it
# repacks, and only afterwards moves the device to FASTBOOT itself
# (tools/ph-build.sh:2400-2436). Declaring FASTBOOT here refused a
# correctly-booted phone and sent the device to the one state where tkboot
# cannot reach it to seed the base image -- the same circular refusal the
# `kernel` rung exists to avoid.
_add(Op("boot",
        "build the dtb, repack and RAM-boot it -- no packaging step",
        needs_state=BOOTED, needs=_KERNEL_INPUTS,
        produces=("boot.img",), reversible=True, disk_gb=5.0))

# `fast` and `upgrade` end in `pmbootstrap export`, which packs boot.img out
# of a rootfs chroot a full install has populated. `kernel` is deliberately
# NOT here: it RUNS install, so it is the rung that FIXES an uninstalled
# chroot rather than one that needs it fixed. Gating it here made porthole
# refuse `kernel` with its own advice, unrunnable.
_add(Op("fast",
        "build the kernel and flash boot only, UUIDs untouched",
        needs_state=BOOTED, needs=_KERNEL_INPUTS + (CHROOT_INSTALLED,),
        produces=("boot.img", "kernel.apk"),
        destroys=("the boot partition",), reversible=False, disk_gb=20.0))

_add(Op("upgrade",
        "swap to a DIFFERENT kernel flavor: push modules, flash boot",
        needs_state=BOOTED, needs=_KERNEL_INPUTS + (CHROOT_INSTALLED,),
        produces=("boot.img",),
        destroys=("the boot partition",), reversible=False, disk_gb=20.0))

# tkbuild makes no ssh or fastboot call (tools/ph-build.sh:952-1010) -- same
# device-interaction profile as `image` (none), so the same NONE.
_add(Op("kernel",
        "build the kernel, package it, install and verify, but NOT flash",
        needs_state=NONE, needs=_KERNEL_INPUTS + (ROOTFS_PW,),
        produces=("rootfs chroot", "boot.img"), reversible=True, disk_gb=25.0))

# The only rung that compiles no kernel tree, and therefore the one a freshly
# set-up host can actually run.
#
# `image` and `install` both wrap `_ph_install_rootfs` (tools/ph-build.sh:884)
# and both replace the rootfs image on disk -- functionally the same host-side
# act, so they share destroys/reversible. Rebuilding regenerates the artefact
# that gets overwritten, so that is not the irreversible act; `flash-full` is,
# and is the only op strict enough to earn reversible=False.
_add(Op("image",
        "build the whole system image from pmaports -- no kernel tree",
        needs_state=NONE, needs=(KERNEL_PKG, ARCH, DTB, WORKDIR, ROOTFS_PW),
        produces=("rootfs chroot", "rootfs.img"),
        destroys=("the previous rootfs image",), reversible=True,
        disk_gb=25.0))

_add(Op("clean", "unstack /mnt/linux binds", needs_state=NONE, disk_gb=0.0))
_add(Op("purge", "remove envkernel apks that outrank a release",
        needs_state=NONE, destroys=("local dev snapshots",), disk_gb=0.0))

# ------------------------------------------------------------- the flashes --

_add(Op("flash-boot",
        "flash the boot image only, leaving the rootfs alone",
        needs_state=FASTBOOT, needs=(DTB,),
        destroys=("the boot partition",), reversible=False, disk_gb=0.0))

# The operation the whole rework exists for. It needs a rootfs image, and only
# a site that can make one may run it.
_add(Op("flash-full",
        "flash the rootfs AND the boot image -- replaces everything on the "
        "device",
        needs_state=FASTBOOT, needs=(DTB,),
        sites={SANDBOX: NO_LOOP, HOST: True},
        destroys=("the device rootfs", "every locally-installed package",
                  "the boot partition"),
        reversible=False, disk_gb=0.0))

_add(Op("install",
        "mint a fresh rootfs image from pmaports and the local repo",
        needs_state=NONE, needs=(KERNEL_PKG, ARCH, WORKDIR, ROOTFS_PW),
        sites={SANDBOX: NO_LOOP, HOST: True},
        produces=("rootfs.img",),
        destroys=("the previous rootfs image",),
        reversible=True, disk_gb=25.0))


# -------------------------------------------------------------- the checks --

# What to say when an input is missing. The fix, not just the name: a message
# that names a variable and not where to set it sent people to read a
# committed profile that correctly says nothing about their working repo.
_MISSING = {
    TREE: ("no kernel tree -- this rung compiles one. `porthole build "
           "image` needs no tree; it builds the whole system from "
           "pmaports"),
    KERNEL_PKG: "PORTHOLE_KERNEL_PKG is not set in the profile",
    DEFCONFIG: "PORTHOLE_DEFCONFIG is not set in the profile",
    ARCH: "PORTHOLE_ARCH is not set in the profile",
    DTB: "PORTHOLE_DTB is not set in the profile",
    WORKDIR: ("no working repo for this device -- `porthole init` sets it, "
              "or `porthole use <codename> --workdir <path>`"),
    ROOTFS_PW: ("TK_PMOS_PASSWORD is unset -- `pmbootstrap install` sets the "
                "rootfs user's password and this operation runs it. Export it "
                "(a variable on purpose: a flag would show it in `ps`)"),
    CHROOT_INSTALLED: ("no rootfs chroot has been installed in this work dir, "
                       "and this rung packs boot.img out of one -- run "
                       "`porthole build kernel` once against it"),
}


def unmet(op: Op, facts: dict) -> list[str]:
    """Everything wrong, reported together. Pure.

    Together rather than one at a time: an envkernel build is minutes long,
    and finding out about the second missing value after fixing the first is
    how an afternoon goes.
    """
    problems = []
    want = op.needs_state
    got = facts.get("state", "")
    # An empty/unknown state is not a mismatch: a preview has to render
    # without probing the device, and gating it behind a state check would
    # put the probe back that this rework exists to remove. Leave this be.
    if want in (BOOTED, FASTBOOT) and got and got != want:
        problems.append(
            f"the device is {got}, not {want} -- this operation needs it "
            f"{want}")
    for key in op.needs:
        if not facts.get(key):
            problems.append(_MISSING[key])
    free = facts.get("free_gb")
    if free is not None and free < op.disk_gb:
        problems.append(
            f"{free:.1f} GB free where this operation needs {op.disk_gb:.0f} "
            f"GB to finish -- `porthole disk` says what is prunable")
    return problems


def op(name: str) -> Op:
    return OPS[name]
