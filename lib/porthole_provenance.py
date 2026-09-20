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
    # --no-network, because this reads the INSTALLED database and must not
    # depend on reaching a mirror. Measured 2026-09-20 on a freshly flashed
    # device, which has no wifi credentials yet: apk spent its time on DNS,
    # printed three "transient error" warnings, and the grep found nothing --
    # so provenance reported "the device did not answer, so its kernel is
    # unknown" about a phone that was answering fine. A check that fails
    # BECAUSE the device was just reflashed is useless exactly when it is
    # most wanted.
    "apk info -v --no-network 2>/dev/null | "
    "grep -E '^linux-(postmarketos|[a-z]+-)' || true; "
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


# -- the USERSPACE half ----------------------------------------------------
#
# The kernel is not the only thing that drifts, and on 2026-09-20 it was not
# even the worst one. The phone carried device-google-taimen r60 while the
# checkout was at r62. The two releases in between contained the fix for an
# on-screen keyboard that unfolded over every Settings page -- written,
# committed, reviewed, and simply not on the phone. brief said nothing,
# because provenance covered only the kernel package.
#
# The device package is where a port keeps its dconf databases, udev rules,
# modprobe options and service units, so a stale one is indistinguishable
# from a bug in whatever it configures. That is precisely how this one was
# read: as a keyboard regression.
def installed_versions(dev, names) -> dict:
    """`{name: "pkgver-rN"}` for the named packages. {} if the device is mute.

    Queried BY NAME rather than by dumping `apk info -v`: the full list is
    about 1200 lines, and a prefix match there would happily attribute
    device-google-taimen-fingerprint's release to device-google-taimen.
    """
    wanted = [n for n in names if n]
    if not wanted:
        return {}
    # --no-network: see PROBE. A freshly flashed phone has no network yet.
    out = dev.run("apk info -v --no-network 2>/dev/null", timeout=30) or ""
    if not out.strip():
        return {}
    found = {}
    for line in out.splitlines():
        entry = line.strip()
        for name in wanted:
            # Exact: everything up to the LAST two dash-separated fields is
            # the package name, so `-fingerprint` cannot match its parent.
            if entry.startswith(name + "-"):
                rest = entry[len(name) + 1:]
                if "-r" in rest and "-" not in rest.split("-r")[0]:
                    found.setdefault(name, rest)
    return found


def compare_packages(installed: dict, checkout: dict):
    """`(state, evidence)` for device-owned packages. Pure.

    Only reports packages present in BOTH maps. A package the checkout does
    not describe is not drift, and one the phone does not have is a missing
    install rather than a stale one -- a different problem with a different
    fix, and conflating them produces a warning nobody can act on.
    """
    if not installed or not checkout:
        return "skip", "no package versions to compare"
    stale = []
    for name, want in sorted(checkout.items()):
        have = installed.get(name)
        if have and want and have != want:
            stale.append(f"{name}: phone {have}, checkout {want}")
    if not stale:
        return "done", "device packages match the checkout"
    return "todo", ("STALE DEVICE PACKAGES -- " + "; ".join(stale) +
                    ". Whatever those releases changed is NOT on the phone, "
                    "and a fix you cannot see looks exactly like a bug.")


def checkout_versions(cfg, names) -> dict:
    """`{name: "pkgver-rN"}` from the pmaports checkout. {} when unresolvable.

    Reuses porthole_cmd_pkg's APKBUILD reader rather than parsing a second
    time: two parsers for one file format is how they drift apart.
    """
    import pathlib

    import porthole_cmd_pkg as pkg
    import porthole_pmaports as pmap

    # find_pmaports, not cfg["PORTHOLE_PMAPORTS"]: four things can decide
    # where pmaports is, and `porthole cd pmaports` on this desk answers
    # pmbootstrap's cache_git clone rather than the working checkout. Reading
    # the wrong tree would compare the phone against somebody else's aports
    # and report drift that is not there -- or worse, miss drift that is.
    root = pmap.find_pmaports(cfg)
    if root is None:
        return {}
    root = pathlib.Path(root)
    out = {}
    for name in names:
        if not name:
            continue
        where = pkg.find_aport(root, name)
        if where:
            version = pkg.apkbuild_version(where)
            if version:
                out[name] = version
    return out


# -- the tree the package is NOT built from --------------------------------
#
# A kernel aport builds from its PATCH SERIES, not from the checkout. So an
# uncommitted edit in the kernel tree is present in every `mod`/`boot` rung
# -- which compile the tree -- and absent from every `fast`/`kernel`/`image`
# rung, which rebuild the package. The edit does not fail; it evaporates.
#
# This repo has already paid for it once: "aport patch series NOT regenerated
# -- a kernel pkg rebuild reverts display". With several agents on several
# worktrees of one tree it stops being an edge case. On 2026-09-20 the
# registered tree carried two uncommitted files while an image was built from
# the series, and nothing anywhere said so.
def dirty_tree(path) -> dict:
    """`{path, dirty, branch}` for a checkout, or {} when it is not one.

    Reuses porthole_cmd_workspace.survey rather than shelling out to git
    again: one definition of "dirty", so the two cannot disagree about it.
    """
    import pathlib

    import porthole_cmd_workspace as ws

    if not path:
        return {}
    where = pathlib.Path(path)
    if not (where / ".git").exists():
        return {}
    row = ws.survey(where)
    return {"path": str(where), "dirty": row["dirty"], "branch": row["branch"]}


def compare_tree(tree: dict):
    """`(state, evidence)` for uncommitted work in the kernel tree. Pure."""
    if not tree:
        return "skip", "no kernel tree to inspect"
    if not tree["dirty"]:
        return "done", "the kernel tree is clean"
    return "todo", (
        "UNCOMMITTED WORK in {} on {}: {} file(s). A package build takes the "
        "aport's PATCH SERIES, not this tree, so those edits are in a `mod` "
        "or `boot` rung and absent from `fast`, `kernel` and `image` -- they "
        "do not fail, they evaporate.".format(
            tree["path"], tree["branch"], tree["dirty"]))


# -- what we BUILD, which cannot rot the way a manifest can ------------------
#
# compare_packages() above is only as honest as the list it is given, and the
# first list it was given was the aports.conf manifest. That manifest names 15
# aports; the tree carries 27 (docs/DESIGN-pmaports-upstream-refork.md §3).
# So sns-reg, rmtfs, neard, modemmanager, chromium and seven others could go
# stale on the phone with the gate saying nothing -- the same rot the manifest
# already shows in both directions, declaring an fprintd that does not exist
# and not declaring a chromium that does.
#
# The local package repo cannot rot that way. It is the set of things this
# host has actually built, so a fork nobody remembered to declare is in it by
# construction. Measured 2026-09-20: this is what would have caught
#
#     mesa  26.2.2-r51 built here,  26.2.3-r0 installed on the phone
#
# where apk took stock because it compares pkgver before pkgrel and upstream
# moved 26.2.2 -> 26.2.3, dropping fifty-one releases of a5xx patches and
# bringing back display corruption that read as a new bug.
#
# `porthole pkg drift` cannot see it: that compares the CHECKOUT against
# upstream aports and never asks the device.
_APK_NAME = re.compile(r"^(?P<name>.+?)-(?P<ver>\d[^-]*(?:-r\d+)?)\.apk$")


def built_packages(cfg) -> dict:
    """`{name: "pkgver-rN"}` for every apk this host has built. Newest wins.

    Pure apart from the directory read. An empty result means "no local repo
    to compare against", which compare_packages() reports as `skip`.
    """
    import pathlib

    pmb = cfg.get("PORTHOLE_PMB_DIR", "")
    arch = cfg.get("PORTHOLE_ARCH", "")
    if not (pmb and arch):
        return {}
    repo = pathlib.Path(pmb) / "packages" / "edge" / arch
    if not repo.is_dir():
        return {}
    out = {}
    for apk in repo.glob("*.apk"):
        hit = _APK_NAME.match(apk.name)
        if not hit:
            continue
        name, ver = hit.group("name"), hit.group("ver")
        # Several pkgrels of one package accumulate here. The newest file is
        # the one a build just produced and therefore the one the device is
        # expected to be carrying; an older sibling is not drift.
        prev = out.get(name)
        if prev is None or apk.stat().st_mtime > prev[1]:
            out[name] = (ver, apk.stat().st_mtime)
    return {name: ver for name, (ver, _mtime) in out.items()}


def undeclared_carries(built: dict, declared) -> list:
    """Packages this host builds that the manifest never mentions. Pure.

    Detection only. The manifest is where a human says WHY a fork is carried,
    and nothing here can write that line -- but it can stop the omission being
    invisible, which is the failure the refork design names as membership
    drift.
    """
    known = {n for n in (declared or []) if n}
    # Subpackages are not separate carries: device-google-taimen-fingerprint
    # is the device package. Attribute a name to its longest declared prefix
    # before calling it undeclared.
    missing = []
    for name in sorted(built):
        if name in known:
            continue
        if any(name.startswith(k + "-") for k in known):
            continue
        missing.append(name)
    return missing


# -- the one that cannot rot: ask apk which repo actually won ---------------
#
# Every list-based check inherits the list's rot. aports.conf names 15 aports
# and the tree carries 27; the local package repo is whatever this host
# happened to build and had mesa at neither version. So the list is the wrong
# place to stand.
#
# apk already knows. `apk policy <pkg>` names every version on offer and which
# repository each came from, and marks the installed one. On 2026-09-20:
#
#     mesa policy:
#       26.2.2-r51:
#         https://github.com/porthole-dev/pmos-packages/...   <- ours
#       26.2.3-r0:
#         lib/apk/db/installed                                <- INSTALLED
#         http://dl-cdn.alpinelinux.org/alpine/edge/main
#
# That is the whole bug, in output apk was already willing to produce: our
# fork was on offer and stock won, because apk compares pkgver before pkgrel
# and upstream moved 26.2.2 -> 26.2.3. No manifest, no build directory and no
# declared list is needed to see it -- only the question "did anything we
# publish lose to something we did not".
#
# One call for the whole system: 5341 lines for 1247 packages, measured.
POLICY_PROBE = "apk info 2>/dev/null | xargs apk policy 2>/dev/null"


def parse_policy(text: str, ours: str = "porthole-dev") -> list:
    """`[{name, installed, ours}]` for packages where OUR repo lost. Pure.

    `ours` is matched as a substring of the repository URL, so it works for
    any fork host without the caller naming a full URL.
    """
    drifted, name, versions = [], "", {}

    def flush():
        if not name:
            return
        installed, from_foreign = "", False
        for version, srcs in versions.items():
            if not any("db/installed" in s for s in srcs):
                continue
            installed = version
            # WHICH repo also offers the installed version decides whether
            # this is the bug or its harmless mirror image. A version served
            # by a repository that is not ours means stock outranked the
            # fork, which is the failure. A version served by nothing but
            # db/installed was put there by hand and is simply AHEAD of what
            # we publish -- true of every package deployed straight to the
            # phone, and not drift. Flagging those made the first run of this
            # report 21 lines of which 2 were real, and a report that is 90%
            # noise is one nobody reads.
            from_foreign = any(("://" in s and ours not in s) for s in srcs)
        mine = sorted(v for v, srcs in versions.items()
                      if any(ours in s for s in srcs))
        if installed and mine and installed not in mine and from_foreign:
            drifted.append({"name": name, "installed": installed,
                            "ours": mine[-1]})

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if not line.startswith(" ") and line.endswith("policy:"):
            flush()
            name, versions = line[:-len(" policy:")].strip(), {}
        elif line.endswith(":") and line.strip().rstrip(":") and name:
            versions[line.strip().rstrip(":")] = []
        elif versions and name:
            versions[list(versions)[-1]].append(line.strip())
    flush()
    return drifted


def compare_policy(drifted: list):
    """`(state, evidence)` for forks that lost to another repository. Pure."""
    if not drifted:
        return "done", "every package we publish is the one installed"
    # Collapse subpackages. mesa, mesa-gl, mesa-egl, mesa-dri-gallium and
    # mesa-vulkan-freedreno are one fork losing once, and listing them
    # separately turned a two-line finding into nine.
    groups = {}
    for d in drifted:
        groups.setdefault((d["installed"], d["ours"]), []).append(d["name"])
    parts = []
    for (installed, mine), names in sorted(groups.items()):
        head = min(names, key=len)
        extra = " (+{} subpackages)".format(len(names) - 1) if len(names) > 1 else ""
        parts.append("{}{}: running {}, we publish {}".format(
            head, extra, installed, mine))
    worst = "; ".join(parts)
    return "todo", (
        "OUR FORKS LOST TO ANOTHER REPO -- " + worst +
        ". apk compares pkgver before pkgrel, so a fork is outranked the "
        "moment upstream's pkgver moves; the patches vanish with no message "
        "and the bugs they fix come back looking new.")
