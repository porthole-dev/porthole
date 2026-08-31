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
    "echo '<<>>'; ls /lib/modules 2>/dev/null | tr '\\n' ' '"
)


def running(dev, kpkg: str = "") -> dict:
    """What the device says about its kernel. One ssh round trip.

    Returns {} when the device did not answer, which the caller must treat as
    unknown rather than as a tree build.
    """
    out = dev.run(PROBE, timeout=20) or ""
    if not out.strip():
        return {}
    parts = (out.split("<<>>") + ["", "", ""])[:3]
    version, installed, modules = (p.strip() for p in parts)
    apk_pkgrel = ""
    for line in installed.splitlines():
        name = line.strip()
        if kpkg and not name.startswith(kpkg + "-"):
            continue
        if "-r" in name:
            apk_pkgrel = name.rsplit("-r", 1)[-1]
            break
    # /proc/version holds utsname.version after the compiler string; the `#`
    # is where it starts.
    stamp = version[version.index("#"):] if "#" in version else ""
    return {"proc_version": version, "build_version": stamp,
            "apk_pkgrel": apk_pkgrel, "modules": modules}


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
