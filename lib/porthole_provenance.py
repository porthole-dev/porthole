# SPDX-License-Identifier: MIT
"""What kernel is this phone running, and where did it come from?

WHY
    An agent built one module from a tree, flashed nothing, and later moved to
    venus work expecting the aport's 19 venus patches to be present. They were
    not. The evidence available at the time: no /dev/video7, no venus module,
    and NOTHING in dmesg or the journal -- an entirely silent absence. The
    build verb warns about which tree it is about to build; nothing said a
    word about what the DEVICE is running.

THE STAMP ALREADY EXISTS
    The kernel APKBUILD's build() runs

        make ... KBUILD_BUILD_VERSION="$((pkgrel + 1))-$_flavor"

    so `uname -v` on an aport build reads `#22-postmarketos-qcom-msm8998-7.2`
    -- pkgrel 21, named. An envkernel build never runs that line, because
    `pmbootstrap build --envkernel` packages objects the tree already
    compiled, so it carries the tree's own .version counter and no flavor.
    That is a clean discriminator and it has been on every device all along.

WHY NOT A NEW STAMP
    CONFIG_LOCALVERSION was the obvious proposal and it is the wrong one: it
    moves kernel.release, therefore /lib/modules/<release>, so every module
    already on the phone becomes ABSENT rather than stale on the first switch.
    A device-side regression bought for a host-side display improvement.

Everything here is pure except `running()`, which is one ssh round trip.
"""
from __future__ import annotations

import re

from porthole import slot_suffix

# `#22-postmarketos-qcom-msm8998-7.2 SMP PREEMPT ...`. Anchored, because the
# number has to be the build version and not any `#N` further along the line.
_STAMP = re.compile(r"^#(\d+)(?:-(\S+))?")


def parse_build_version(text: str) -> dict:
    """`uname -v` -> {kind, pkgrel, flavor}. Never guesses.

    kind is `aport` (a flavor suffix is present, so the APKBUILD stamped it),
    `tree` (a bare #N: an envkernel build), or `unknown`.
    """
    match = _STAMP.match((text or "").strip())
    if not match:
        return {"kind": "unknown", "pkgrel": None, "flavor": ""}
    number, flavor = int(match.group(1)), match.group(2) or ""
    if not flavor:
        return {"kind": "tree", "pkgrel": None, "flavor": ""}
    # The APKBUILD stamps pkgrel + 1, so #22 is r21. Reading it as 22 would
    # report every device as one release AHEAD of the checkout, which is worse
    # than reporting nothing.
    return {"kind": "aport", "pkgrel": number - 1, "flavor": flavor}


# One command, not five. Framed with a separator the shell will not produce
# by accident, so a probe that prints nothing is distinguishable from one that
# did not run -- brain/laws/empty-must-mean-unknown-never-changed.md.
PROBE = (
    "cat /proc/version; echo '<<>>'; "
    "apk info -v 2>/dev/null | grep -E '^linux-(postmarketos|[a-z]+-)' || true; "
    "echo '<<>>'; ls /lib/modules 2>/dev/null | tr '\\n' ' '; "
    # The slot, from the running kernel rather than the profile: the profile
    # records which slot we INTEND to use, and the bootloader's A/B retry
    # counter can land the phone on the other one without asking. Reading the
    # intent would then hash the wrong partition and call it drift.
    "echo '<<>>'; cat /proc/cmdline"
)


def running(dev, kpkg: str = "") -> dict:
    """What the device says about its kernel. One ssh round trip.

    Returns {} when the device did not answer, which the caller must treat as
    unknown rather than as a tree build.
    """
    out = dev.run(PROBE, timeout=20) or ""
    if not out.strip():
        return {}
    parts = (out.split("<<>>") + ["", "", "", ""])[:4]
    version, installed, modules, cmdline = (p.strip() for p in parts)
    # Match by NAME, exactly. The grep above also lets linux-firmware-* and
    # linux-pam-* through, and a first-line-wins fallback would silently
    # attribute another package's pkgrel as the kernel's -- a guess dressed as
    # a measurement, worse than reporting nothing. Without a kpkg to match,
    # apk_pkgrel stays empty; compare()'s apk_rel.isdigit() guard then skips
    # the desync branch instead of inventing one.
    apk_pkgrel = ""
    if kpkg:
        for line in installed.splitlines():
            name = line.strip()
            if name.startswith(kpkg + "-") and "-r" in name:
                apk_pkgrel = name.rsplit("-r", 1)[-1]
                break
    # /proc/version holds utsname.version after the compiler string; the `#`
    # is where it starts.
    stamp = version[version.index("#"):] if "#" in version else ""
    return {"proc_version": version, "build_version": stamp,
            "apk_pkgrel": apk_pkgrel, "modules": modules,
            "slot": slot_suffix(cmdline)}


def compare(running_info: dict, pkgver: str, pkgrel: str):
    """`(state, evidence)` for what the device is running. Pure.

    `state` is a plain string so this module never imports the milestone
    table; the caller maps it to a Verdict.
    """
    if not running_info:
        return "blocked", "the device did not answer, so its kernel is unknown"
    parsed = parse_build_version(running_info.get("build_version", ""))
    if parsed["kind"] == "unknown":
        return "blocked", ("could not read a build stamp from /proc/version, "
                           "so the running kernel's lineage is unknown")
    if parsed["kind"] == "tree":
        return "blocked", (
            "tree build (no aport flavor in the build stamp) — the aport's "
            "patch series is NOT necessarily present, and its absence is "
            "silent: no module, no /dev node, nothing in dmesg")

    running_rel = parsed["pkgrel"]
    apk_rel = running_info.get("apk_pkgrel") or ""
    # `fast` flashes boot only and never touches the device's rootfs, so the
    # apk database keeps naming the release the rootfs was last INSTALLED
    # with. uname disagreeing with it is exactly boot/rootfs desync, which the
    # LADDER already names as the trigger for the `kernel` rung.
    if apk_rel.isdigit() and int(apk_rel) != running_rel:
        return "todo", (
            "boot and rootfs are DESYNCED: boot carries aport r{}, the "
            "rootfs apk database says r{}. `porthole build kernel --yes` "
            "then flash, or they keep drifting.".format(running_rel, apk_rel))
    if str(running_rel) == str(pkgrel):
        return "done", "aport r{} ({}), matching the checkout".format(
            running_rel, pkgver)
    return "todo", (
        "running aport r{}; the checkout is at r{} — flash to catch up".format(
            running_rel, pkgrel))


# -- CONTENT, not just pkgrel ----------------------------------------------
#
# WHY THIS EXISTS, and it is the expensive one.
#
# compare() above reasons in pkgrel, and a pkgrel is a LABEL. On 2026-09-20
# `porthole brief` reported
#
#     kernel  done -- aport r79 (7.2.2), matching the checkout
#
# while the DTB on the phone and the DTB the tree builds differed by 8 bytes:
# one power-domains entry, the fix for a camera that had been broken all
# session. Nothing was lying; the label really did match. The CONTENT did not,
# and no pkgrel can see that. The same blindness hid a 30 fps regression --
# the phone had drifted from the tree and was missing the rd_ptr pageflip
# work, which cost hours of GPU theorising for a problem that was not the GPU.
#
# NOT THE LIVE FDT. /sys/firmware/fdt is the obvious probe and it is wrong:
# the bootloader fixes up /chosen and /memory, so it measured 99564 bytes
# against the tree's 97251 and would report a mismatch on every healthy boot.
# A check that cries wolf every time is worse than no check, because it
# teaches people to pass --stale-ok by reflex.
#
# The right artifact is the DTB inside the ACTIVE SLOT'S BOOT PARTITION: the
# same kind of object as the tree's .dtb, so equality is meaningful. Verified
# on taimen -- tree, /boot and the boot_b partition all hash
# 8923640dc264e4c5 at 97251 bytes.
#
# The partition is read whole. It is 20 MB of LOCAL I/O on the phone and
# measured at about two seconds; a sliding-window scan to save that was
# written first, got the buffer-trim offsets subtly wrong, and bought
# nothing a caller can feel.
DTB_PROBE = r"""
import hashlib, struct, sys
data = open("/dev/disk/by-partlabel/boot_" + sys.argv[1], "rb").read()
at = data.find(bytes.fromhex("d00dfeed"))
if at >= 0:
    size = struct.unpack_from(">I", data, at + 4)[0]
    blob = data[at:at + size]
    if len(blob) == size:
        print("%s %d" % (hashlib.sha256(blob).hexdigest(), size))
"""


def dtb_on_device(dev, slot: str) -> dict:
    """`{sha, size}` for the DTB in the active slot, or {} if unreadable.

    {} means UNKNOWN and the caller must say so rather than assume a match --
    brain/laws/empty-must-mean-unknown-never-changed.md.
    """
    if not slot:
        return {}
    script = DTB_PROBE.replace("'", "'\\''")
    out = dev.run("sudo -n python3 -c '{}' {}".format(script, slot),
                  timeout=120) or ""
    parts = out.strip().split()
    if len(parts) != 2 or len(parts[0]) != 64 or not parts[1].isdigit():
        return {}
    return {"sha": parts[0], "size": int(parts[1])}


def dtb_in_tree(path) -> dict:
    """`{sha, size}` for a built DTB on the host, or {} when absent."""
    import hashlib
    import pathlib

    blob = pathlib.Path(path)
    if not path or not blob.is_file():
        return {}
    data = blob.read_bytes()
    return {"sha": hashlib.sha256(data).hexdigest(), "size": len(data)}


def compare_dtb(device: dict, tree: dict):
    """`(state, evidence)` for the device tree blob. Pure.

    `skip` means there was nothing to compare, which is NOT a pass: the caller
    keeps whatever verdict the pkgrel comparison reached and says no more.
    """
    if not tree:
        return "skip", ("no built DTB on this host to compare against -- "
                        "build the tree, or this says nothing")
    if not device:
        return "blocked", ("could not read the DTB out of the active slot, "
                           "so what the phone boots is unknown")
    if device["sha"] == tree["sha"]:
        return "done", "dtb {} ({} bytes) matches the tree".format(
            device["sha"][:8], device["size"])
    return "todo", (
        "DTB CONTENT DRIFT: the phone boots {} ({} bytes), the tree builds "
        "{} ({} bytes). The pkgrel can still match -- it is a label, not the "
        "content. Flash, or measurements describe a kernel you did not "
        "build.".format(device["sha"][:8], device["size"],
                        tree["sha"][:8], tree["size"]))


def tree_dtb_path(cfg: dict) -> str:
    """Where `make dtbs` leaves this device's blob, from the config layer.

    Mirrors ph-build.sh's _PH_DTB_BUILT exactly:
        $_PH_OUT/arch/$PORTHOLE_ARCH_DIR/boot/dts/${PORTHOLE_DTB%/*}/$FILE
    Returns "" when the profile does not describe a DTB, so compare_dtb()
    reports `skip` rather than inventing a path that does not exist.
    """
    import os

    tree = cfg.get("PORTHOLE_KERNEL_TREE", "")
    arch = cfg.get("PORTHOLE_ARCH_DIR", "")
    dtb = cfg.get("PORTHOLE_DTB", "")
    name = cfg.get("PORTHOLE_DTB_FILE", "")
    if not (tree and arch and dtb and name):
        return ""
    out = cfg.get("PORTHOLE_KERNEL_OUT") or os.path.join(tree, ".output")
    return os.path.join(out, "arch", arch, "boot", "dts",
                        dtb.rsplit("/", 1)[0], name)
