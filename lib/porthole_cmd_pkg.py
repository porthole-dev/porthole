#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole pkg` -- build a userspace aport, and say where it is.

WHY THIS EXISTS
    `porthole build` is the envkernel loop and nothing else: every one of its
    rungs produces a kernel artifact. A userspace aport had no verb at all, so
    the only route was

        porthole sandbox shell --command 'pmbootstrap build --lax <aport>'

    which is a bare command runner -- no tracker, no status file, no ETA, no
    log, no completion signal. The result was backwards: a 40-second module
    push had a progress bar, and a multi-hour webkit build reported nothing.

    On 2026-08-30 that cost half an hour. A `webkit2gtk-6.0` build died after
    ninety seconds and nobody noticed, because there was nowhere to look. The
    question that surfaced it was "why can't I see the progress bar".

    See docs/HANDOFF-package-builds.md.

WHAT IT DOES NOT DO
    It does not reimplement the progress machinery. `porthole_progress` grew a
    second SOURCE -- ninja's `[N/M]` -- and this verb reuses the tracker, the
    status file, the log and the failure tail that `porthole build` already
    had. The one genuinely new thing here is that a package build's percentage
    is real: ninja states its total, so nothing has to be inferred from
    history the way a kernel build must.
"""
from __future__ import annotations

import contextlib
import difflib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

from porthole_cli import Bail, EX_FAIL, EX_LOCK, EX_OK, EX_UNAVAILABLE, EX_USAGE

# Four hours. webkit is the reason: it is measured in hours on this hardware,
# and a timeout that kills it at the default half hour would be a tool that
# only works on packages nobody needed help with.
DEFAULT_TIMEOUT = 4 * 60 * 60

# Where an aport can live. Two levels because device aports are nested
# (device/testing/<name>) and everything else is not (temp/<name>,
# main/<name>). Same lookup tk-pkgcheck.sh does, for the same reason: which
# tree a package lives in is not something the caller should have to know.
_APORT_GLOBS = ("*/{}/APKBUILD", "*/*/{}/APKBUILD")

_FIELD = re.compile(r'^(pkgname|pkgver|pkgrel)=["\']?([^"\'#\s]+)', re.M)

# Any simple `name=value` assignment, for expanding the ones the three fields
# above refer to. Kernel aports almost universally write
#
#     _flavor="postmarketos-qcom-msm8998-7.2"
#     pkgname=linux-$_flavor
#
# so reading pkgname literally yields `linux-$_flavor`, and everything
# downstream then looks for an apk by that name and concludes a package that
# built perfectly well was never built.
_ASSIGN = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)=["\']?([^"\'#\n]*)', re.M)
_VAR = re.compile(r'\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?')

# pmb/parse/_apkbuild.py reads an APKBUILD LINE BY LINE and never executes the
# shell. So a dependency appended inside a case/esac -- which is how Alpine
# itself writes a per-arch dependency -- is invisible to pmbootstrap, is never
# installed into the buildroot, and the build dies much later inside cmake
# complaining about a library that `apk search` says exists. That reads as a
# broken distro package rather than a parser limitation, which is why it cost
# a full ninety-second webkit configure before anyone suspected the APKBUILD.
_DEP_VARS = ("makedepends", "depends", "checkdepends")

# `makedepends="$makedepends libjxl-dev"` -- an APPEND, which is the only form
# a case/esac can use and the only form pmbootstrap's line-by-line parser
# drops on the floor.
_APPEND = re.compile(
    r"^\s*(makedepends|depends|checkdepends)=[\"']?\$\1\s+([^\"'\n]*)", re.M)


# The buildroot mutex lives in porthole_buildroot: `pmbootstrap checksum`
# destroyed a kernel build on the redfin port, so a lock only this verb takes
# is not a lock. Re-exported here because the tests and the call sites below
# read better with the short names.
from porthole_buildroot import (  # noqa: E402
    foreign_build, hold, is_free, lock_holder, running_build)

def _find_pmaports(ctx):
    import porthole_pmaports as pmaports

    found = pmaports.find_pmaports(ctx.cfg)
    if not found:
        raise Bail("no pmaports checkout found", EX_FAIL,
                   "`porthole doctor` names how to get one")
    return found


def find_aport(pmaports: pathlib.Path, name: str):
    """The aport directory for `name`, or None. Pure, so it is testable."""
    for pattern in _APORT_GLOBS:
        for hit in sorted(pmaports.glob(pattern.format(name))):
            return hit.parent
    return None


# How many hits a listing prints before it stops being a listing. `pkg search
# lib` matches two thousand packages, and a screen of those is not an answer.
LIST_CAP = 40


# ponytail: names only, never subpackages. `libgstreamer` is built by
# `gstreamer` and is declared inside its APKBUILD, so indexing subpackages
# means READING 14,000 files instead of stat-ing 14,000 directories -- and
# the origin package is the one you fork and build regardless. Parse
# `subpackages=` here if a miss ever costs more than the scan would.
def scan_tree(root) -> dict:
    """Every package in an aports-shaped tree, as {name: directory}.

    A directory is a package iff it holds an APKBUILD. Structural, rather
    than a list of category names to keep current: pmaports has cross/,
    modem/ and extra-repos/ sitting next to docs/, and Alpine's tree has
    scripts/ next to community/, so any hand-written list is one upstream
    reorganisation away from lying.

    Two levels deep, because device aports nest (device/testing/<name>) and
    everything else does not -- the same shape `_APORT_GLOBS` looks in.

    Both trees together are 14,000 packages and cost ~70ms, so there is no
    cache here and no staleness bug to go with it.
    """
    found: dict = {}
    if not root:
        return found
    try:
        categories = [e for e in os.scandir(root)
                      if e.is_dir() and not e.name.startswith(".")]
    except OSError:
        return found
    for category in categories:
        try:
            children = [e for e in os.scandir(category.path) if e.is_dir()]
        except OSError:
            continue
        for child in children:
            if os.path.exists(os.path.join(child.path, "APKBUILD")):
                found.setdefault(child.name, pathlib.Path(child.path))
                continue
            try:
                nested = [e for e in os.scandir(child.path) if e.is_dir()]
            except OSError:
                continue
            for grandchild in nested:
                if os.path.exists(os.path.join(grandchild.path, "APKBUILD")):
                    found.setdefault(grandchild.name,
                                     pathlib.Path(grandchild.path))
    return found


def search(pmaports, upstream, text: str):
    """Packages matching `text` across both trees. Returns (hits, guessed).

    Substring first. When nothing contains the text it falls back to
    difflib, because the search that produced this verb was `posh` for
    `phosh`: a substring match answers that with silence, and one stdlib
    call answers it with the package.

    A name in both trees is reported as pmaports', because a pmaports copy
    shadows Alpine's and is the one `pmbootstrap build` will use.
    """
    local, alpine = scan_tree(pmaports), scan_tree(upstream)
    every = sorted(set(local) | set(alpine))
    needle = text.lower()

    # Exact match first, then shortest: every hit contains the text, so the
    # one with least around it is the one that was meant. Alphabetical put
    # `asteroid-calculator` above `gnome-calculator` for the search
    # "calculator", and the follow-up hint names the first row.
    names = sorted((n for n in every if needle in n.lower()),
                   key=lambda n: (n.lower() != needle, len(n), n))
    guessed = False
    if not names:
        # difflib's own ranking is already best-first; do not re-sort it.
        names = difflib.get_close_matches(needle, every, n=8)
        guessed = bool(names)

    hits = []
    for name in names:
        in_pmaports = name in local
        path = local[name] if in_pmaports else alpine[name]
        root = pmaports if in_pmaports else upstream
        hits.append({
            "name": name,
            "tree": "pmaports" if in_pmaports else "alpine",
            "where": f"{path.parent.relative_to(root)}/",
            "path": str(path),
        })
    return hits, guessed


def apkbuild_version(directory) -> str:
    """`pkgver-rpkgrel` for one package, or "". Only ever called on a hit."""
    try:
        text = (pathlib.Path(directory) / "APKBUILD").read_text(errors="replace")
    except OSError:
        return ""
    fields = apkbuild_fields(text)
    if "pkgver" not in fields:
        return ""
    return f"{fields['pkgver']}-r{fields.get('pkgrel', '?')}"


def missing_aport_hint(pmaports, upstream, name):
    """(message, hint) for a name pmaports does not have. Pure, so testable.

    There are three different reasons a build cannot find an aport and the
    old code collapsed all of them into one sentence -- "`porthole aports`
    lists what is there" -- which was not even true: that verb lists the
    packages named after your device. `porthole pkg build posh` therefore
    reported a real failure and then pointed at a command which could not
    have found the package under any circumstances. That dead end is what
    this whole pair of actions came from, so it is fixed at the source.
    """
    if upstream:
        # The same one-level glob aportgen uses, so a name accepted here is a
        # name aportgen will also find.
        hits = sorted(upstream.glob(f"*/{name}"))
        if hits:
            return (f"{name} is Alpine's ({hits[0].parent.name}/), not "
                    f"pmaports' -- `pmbootstrap build` reads pmaports only",
                    f"porthole pkg fork {name} --yes   then build it")
    pool = sorted(set(scan_tree(pmaports)) | set(scan_tree(upstream)))
    close = difflib.get_close_matches(name, pool, n=1)
    if close:
        return (f"no aport named {name}",
                f"did you mean {close[0]}?   "
                f"`porthole pkg search {name}` for the rest")
    return (f"no aport named {name}",
            f"porthole pkg search {name}   searches both aports trees")


def expand_vars(value: str, assignments: dict, depth: int = 4) -> str:
    """Substitute `$var` / `${var}` from other assignments in the same file.

    Bounded and textual, never a shell. Reading an APKBUILD by executing it is
    how a build tool acquires arbitrary code execution from a package it was
    only asked to look at, so a value needing command substitution is left
    unexpanded and rejected by the caller instead.
    """
    for _ in range(depth):
        if "$" not in value:
            break
        value = _VAR.sub(
            lambda m: assignments.get(m.group(1), m.group(0)), value)
    return value


def apkbuild_fields(text: str) -> dict:
    """`pkgname`/`pkgver`/`pkgrel` out of an APKBUILD.

    A regex and not a shell, for the reason expand_vars gives.

    A field that still contains `$` after expansion is DROPPED rather than
    returned literally. Everything downstream turns these three into a
    filename, and a wrong filename does not fail loudly -- it reports that a
    package which built perfectly well was never built. Absent is a state the
    callers already handle ("cannot check"); wrong is not.
    """
    assignments = {k: v.strip() for k, v in _ASSIGN.findall(text)}
    out = {}
    for key, raw in _FIELD.findall(text):
        value = expand_vars(raw, assignments).strip()
        if value and "$" not in value and "`" not in value:
            out[key] = value
    return out


def static_deps(text: str, var: str) -> set:
    """Packages assigned to `var` OUTRIGHT -- the ones pmbootstrap can see.

    An append (`var="$var ..."`) is deliberately excluded: that is the form
    this whole check is about.
    """
    found = set()
    for match in re.finditer(rf"(?m)^\s*{re.escape(var)}=(.*)$", text):
        rest = match.group(1)
        if rest.startswith('"'):
            # A dependency list is usually several quoted lines. Take the whole
            # thing, or a multi-line makedepends reads as one token.
            end = text.find('"', match.start(1) + 1)
            body = text[match.start(1) + 1:end] if end != -1 else rest
        else:
            body = rest.strip('"\'')
        if body.lstrip().startswith("$" + var):
            continue
        found.update(tok for tok in body.split() if not tok.startswith("$"))
    return found


def conditional_dep_warning(text: str) -> str:
    """The pmbootstrap parser trap, or "" when the APKBUILD is safe.

    Checked BEFORE a build rather than after, because the whole cost of this
    trap is that it surfaces ninety seconds into cmake as somebody else's bug.

    Only warns about packages that are appended conditionally and NOT also
    listed statically. The real webkit2gtk-6.0 aport appends `libjxl-dev`
    inside a case/esac AND lists it statically, having already been bitten --
    firing on that would be a warning about correct code, which is how a check
    teaches people to ignore it.
    """
    missing = []
    for var, rest in _APPEND.findall(text):
        visible = static_deps(text, var)
        missing += [tok for tok in rest.split()
                    if not tok.startswith("$") and tok not in visible]
    if not missing:
        return ""
    return (f"{', '.join(sorted(set(missing)))} "
            f"{'is' if len(set(missing)) == 1 else 'are'} appended "
            f"conditionally and not listed statically. pmbootstrap parses "
            f"APKBUILDs line by line and never runs the shell, so this is "
            f"invisible to it and will NOT be installed into the buildroot -- "
            f"the build then fails inside configure naming a library that apk "
            f"says exists. List it in the plain assignment too.")


def expected_apk(packages: pathlib.Path, arch: str, fields: dict):
    """Where the .apk must appear, or None if the APKBUILD did not say.

    The channel is globbed rather than configured: it is a directory name
    pmbootstrap chose, and reading it off disk cannot disagree with reality
    the way a second copy of the setting can.
    """
    name, ver, rel = (fields.get("pkgname"), fields.get("pkgver"),
                      fields.get("pkgrel"))
    if not (name and ver and rel):
        return None
    wanted = f"{name}-{ver}-r{rel}.apk"
    for channel in sorted(packages.glob("*")):
        candidate = channel / arch / wanted
        if candidate.exists():
            return candidate
    # Nothing on disk yet: name where it WILL be, so the failure can say what
    # it looked for instead of only that it did not find it.
    channels = sorted(p.name for p in packages.glob("*") if p.is_dir())
    channel = channels[0] if channels else "edge"
    return packages / channel / arch / wanted


# What pmbootstrap says when it decides not to build. Both lines land in the
# log porthole already writes, and both were there for the phosh run below --
# they just never reached the person who ran it.
_UP_TO_DATE = re.compile(r"Package '([^']+)' is up to date")
_NEWER_BINARY = re.compile(r"about to install (\S+) (\S+) "
                           r"\(local pmaports: ([^,)]+)")


def why_nothing_built(log_text: str, aport: str):
    """`(message, hint)` when pmbootstrap declined to build, else None.

    Measured 2026-08-31: `pkg build phosh` ran 8m49s, exited 0, and porthole
    said "build reported success but phosh-99990.56.0-r0.apk is not there" --
    true, and useless. pmbootstrap had already explained itself twice in the
    same log: the binary repo carries phosh 99990.57.0-r1, NEWER than the
    99990.56.0-r0 in local pmaports, so it built the one dependency that was
    outdated (modemmanager, 662 steps) and skipped phosh itself. Discarding a
    reason the tool stated in plain words and replacing it with "the file is
    not there" is the black box this command exists to end.

    Pure: the parsing is the part that can be wrong, and it is testable
    without an eight-minute build.
    """
    # No ANSI stripping: pmbootstrap colours its terminal output, not the
    # log.txt this reads.
    if not any(m.group(1) == aport for m in _UP_TO_DATE.finditer(log_text)):
        return None
    message = f"{aport}: nothing was built -- pmbootstrap says it is up to date"
    force = f"`porthole pkg build {aport} --force` builds your aport anyway"
    for match in _NEWER_BINARY.finditer(log_text):
        if match.group(1) == aport:
            return (message,
                    f"the binary repo has {match.group(2)}, newer than the "
                    f"{match.group(3)} in your pmaports -- so there was "
                    f"nothing to build. {force}, `pmbootstrap pull` catches "
                    f"pmaports up")
    return (message, force)


def log_since(path, offset: int) -> str:
    """What was appended to pmbootstrap's log after `offset`.

    Bounded on purpose. log.txt is shared and long-lived, and reading it whole
    would let a PREVIOUS run's "is up to date" explain this one -- the same
    defect the follow thread's seek-to-end exists to prevent.
    """
    try:
        with open(path, errors="replace") as handle:
            handle.seek(offset)
            return handle.read()
    except OSError:
        return ""


def outdated(pmaports: pathlib.Path, packages: pathlib.Path, arch: str):
    """`[(name, why)]` for local aports whose .apk no longer matches them.

    Deliberately quiet: it speaks ONLY about packages this machine has already
    built. An aport carried in temp/ that was never built here is not
    "outdated", it is simply not built, and listing eighteen of those would
    train everyone to ignore the whole check.

    Two things count as outdated, and they are different mistakes:
      - the aport declares a pkgver-pkgrel that has no apk  (you bumped it)
      - a file beside the APKBUILD is newer than the apk    (you edited it)

    The second is the one that bit: temp/phoc sat at 0.56.0 while the mirror
    moved on, apk installed the newer stock build, and both GPU-reset patches
    vanished with no message anywhere.
    """
    built = {}
    for apk in packages.glob(f"*/{arch}/*.apk"):
        parts = apk.name[:-4].rsplit("-", 2)
        if len(parts) == 3:
            built.setdefault(parts[0], []).append(apk)

    found = []
    for name, apks in sorted(built.items()):
        directory = find_aport(pmaports, name)
        if directory is None:
            # A subpackage (`-dev`, `-dbg`, `-lang`) or something we do not
            # carry an aport for. Both are correctly silent.
            continue
        try:
            fields = apkbuild_fields(
                (directory / "APKBUILD").read_text(errors="replace"))
        except OSError:
            continue
        want = f"{name}-{fields.get('pkgver')}-r{fields.get('pkgrel')}.apk"
        current = [a for a in apks if a.name == want]
        if not current:
            have = ", ".join(sorted(a.name[len(name) + 1:-4] for a in apks)[:3])
            found.append((name, f"aport says {fields.get('pkgver')}-r"
                                f"{fields.get('pkgrel')}, built {have}"))
            continue
        apk_at = current[0].stat().st_mtime
        newest = max((f.stat().st_mtime for f in directory.iterdir()
                      if f.is_file()), default=0.0)
        if newest > apk_at:
            found.append((name, "edited since it was last built"))
    return found


def container_cmd(aport: str, arch: str, force: bool = False) -> list[str]:
    """The podman line for a package build. Pure, so WHERE it runs is testable
    without podman.

    `--lax` is not a speed knob here, it is the only thing that runs. A
    non-lax `pmbootstrap build` calls zap_buildroots(), which umounts the
    chroot -- and the recursive /dev bind the rootless workspace needs leaves
    propagated sub-mounts a userns cannot umount by path
    (`umount: /pmb/chroot_native/dev/shm: not mounted`, exit 32). The build
    dies at "Zapping buildroots" before it starts. tools/ph-build.sh already
    does this; a hand-rolled pmbootstrap call is what does not, and it is the
    first thing that bites anyone who writes one.
    See brain/findings/what-a-rootless-workspace-cannot-do.md.
    """
    import porthole_cmd_sandbox as sandbox

    argv = ["pmbootstrap", "build", "--lax", shlex.quote(aport),
            "--arch", shlex.quote(arch)]
    if force:
        argv.append("--force")
    return in_container(" ".join(argv))


def in_container(call: str) -> list[str]:
    """Wrap one pmbootstrap command line for the workspace container.

    PYTHONUNBUFFERED is not a nicety, it is what makes `pkg build` work at
    all. pmbootstrap is Python; writing to a pipe rather than a tty it
    switches to block buffering and holds its output until it exits.
    Measured: five minutes into a gst-plugins-good build, with cc and lto1
    visibly running inside the container, the log file was ZERO BYTES and
    the bar had never moved. Every line arrives at the end, which is exactly
    the black box this was built to replace.
    """
    import porthole_cmd_sandbox as sandbox

    return ["podman", "exec", "-e", "PYTHONUNBUFFERED=1", sandbox.CONTAINER,
            "/bin/bash", "-lc", f"cd /porthole && {call}"]


def container_has_upstream() -> bool:
    """Whether the workspace can see Alpine's aports.

    ASKED, not assumed. Hardcoding "the container never has it" would mean
    that adding the mount fixes nothing until somebody also remembers to
    delete a refusal in here, which is how a guard outlives its reason.
    """
    try:
        return subprocess.run(
            in_container("test -d /pmb/cache_git/aports_upstream"),
            capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def fork_cmd(name: str) -> list[str]:
    """The podman line for a fork. Pure, so WHERE it runs is testable.

    aportgen belongs in the container for the same reason a build does, and
    it took a real attempt to find out: on the host, `pmbootstrap aportgen`
    asks the privilege broker to copy an APKINDEX into a cache directory
    that does not exist yet, ph-sudo refuses the unresolvable destination,
    and the fork dies at exit 78 having written nothing. Inside the
    workspace root maps to your own uid and no broker is involved.
    """
    return in_container(
        f"pmbootstrap -y aportgen --fork-alpine {shlex.quote(name)}")


def host_cmd(aport: str, arch: str, lax: bool, force: bool = False) -> list[str]:
    """The host equivalent. Non-lax by default: on a real root filesystem the
    zap is correct, and it is only the rootless workspace that cannot do it."""
    argv = ["pmbootstrap", "build"]
    if lax:
        argv.append("--lax")
    if force:
        argv.append("--force")
    return argv + [aport, "--arch", arch]


# Set for the host path too, where the same buffering applies -- see
# container_cmd. Exported into the child's environment rather than the
# command line because there is no shell in between to accept an assignment.
UNBUFFERED = {"PYTHONUNBUFFERED": "1"}


def _pmb_workdir(ctx, in_container: bool) -> pathlib.Path:
    """Kept as a name because `brief` calls it; the implementation is in
    `porthole_cmd_build`, beside the _workspace_usable decision it depends
    on."""
    import porthole_cmd_build as build

    return build.pmb_workdir(ctx, in_container)


def _packages_dir(ctx, in_container: bool) -> pathlib.Path:
    """Where the built .apk lands, on the HOST filesystem either way.

    A container build writes into the workspace's own pmbootstrap work dir,
    which is bind-mounted at /pmb -- so the artifact is readable from here
    without entering the container, and the verification below needs no
    second podman call.
    """
    import porthole_cmd_sandbox as sandbox

    if in_container:
        return sandbox._sandbox_pmb(ctx.cfg) / "packages"
    host = ctx.cfg.get("PORTHOLE_PMB_DIR") or "~/.local/var/pmbootstrap"
    return pathlib.Path(host).expanduser() / "packages"


def _build(ctx, args) -> int:
    import porthole_cmd_build as build
    import porthole_progress as progress

    aport = args.target
    if not aport:
        raise Bail("which aport?", EX_FAIL,
                   "porthole pkg build <aport>, e.g. `porthole pkg build phoc`")

    arch = args.arch or ctx.cfg.get("PORTHOLE_ARCH") or "aarch64"
    pmaports = _find_pmaports(ctx)
    directory = find_aport(pmaports, aport)
    if directory is None:
        import porthole_pmaports as pmap

        message, hint = missing_aport_hint(
            pmaports, pmap.find_aports_upstream(pmaports), aport)
        raise Bail(message, EX_FAIL, hint)

    text = (directory / "APKBUILD").read_text(errors="replace")
    fields = apkbuild_fields(text)
    ctx.out.kv("aport", str(directory.relative_to(pmaports)), 10)
    ctx.out.kv("version", f"{fields.get('pkgver','?')}-r{fields.get('pkgrel','?')}", 10)

    warning = conditional_dep_warning(text)
    if warning:
        ctx.out(ctx.out.paint(f"  warning: {warning}", "yellow"))

    usable, why_not = build._workspace_usable(ctx)
    workdir = _pmb_workdir(ctx, usable)
    if not args.wait and not is_free(workdir):
        raise Bail(f"the buildroot is busy: "
                   f"{lock_holder(workdir) or 'another build'}", EX_LOCK,
                   "two pmbootstrap builds share one buildroot and delete "
                   "each other's source tree. Wait, or --wait SECONDS.")
    # The lock binds only callers who take it. Anything driving pmbootstrap
    # directly -- `sandbox shell --command`, a hand-rolled podman exec -- holds
    # nothing, and that is how the build this guard exists to protect was
    # started. So ask the container what is actually running as well.
    foreign = running_build(ctx, usable)
    if foreign:
        raise Bail(f"a pmbootstrap build is already running: {foreign}",
                   EX_LOCK,
                   "it holds no lock (started outside `porthole pkg`), but it "
                   "owns the buildroot all the same -- starting now deletes "
                   "its source tree")
    env = dict(os.environ)
    for key, value in ctx.cfg.items():
        if key.startswith(("PORTHOLE_", "TK_")) and isinstance(value, str):
            env[key] = value
    env.update(UNBUFFERED)

    force = getattr(args, "force", False)
    if usable:
        cmd = container_cmd(aport, arch, force=force)
        ctx.out(ctx.out.paint("  building IN THE WORKSPACE (container, --lax)",
                              "cyan"))
    elif shutil.which("pmbootstrap"):
        cmd = host_cmd(aport, arch, bool(env.get("PORTHOLE_LAX_BUILD")),
                       force=force)
        ctx.out(ctx.out.paint(f"  building ON THE HOST ({why_not})", "cyan"))
    else:
        # 69, not 1: there is nothing here that could build, so this is not
        # a statement about the package.
        raise Bail(f"no workspace and no pmbootstrap on PATH ({why_not})",
                   EX_UNAVAILABLE, "run `porthole sandbox up` first")

    # Said every time, not only on --detach: an agent running this build as a
    # background task is the exact case where the bar goes into a log the
    # human never opens. `porthole pkg watch` is the only way back in.
    # (--detach prints its own copy below, alongside the pid/log it just
    # produced, so skip here rather than say it twice in the same run.)
    tty = sys.stdout.isatty()
    if args.dry_run or not args.detach:
        ctx.out(ctx.out.paint(watch_hint(tty), "cyan" if tty else "yellow"))

    if args.dry_run:
        print(" ".join(shlex.quote(a) for a in cmd))
        return EX_OK

    if args.detach:
        return _detach(ctx, args, aport, arch)

    packages = _packages_dir(ctx, usable)  # noqa: E501
    want = expected_apk(packages, arch, fields)
    before = want.exists() and want.stat().st_mtime if want else False

    if usable:
        # Arm the native ccache before building. Without this, ccache in the
        # aarch64 buildroot is itself an emulated binary and every object pays
        # qemu for hashing its preprocessed source. The kernel path has done
        # this since the chroot_native fix; packages never inherited it.
        _arm_ccache(ctx)

    # pmbootstrap keeps the real build output in its own log.txt and puts
    # only high-level `=> step` lines on stdout. Following that file is what
    # makes the ninja `[N/M]` fraction reachable at all -- and what this run
    # appends to it is also the only place pmbootstrap explains a build it
    # decided not to do, so note where the file ends before we start.
    pmb_log = workdir / "log.txt"
    log_end = pmb_log.stat().st_size if pmb_log.exists() else 0
    with hold(workdir, aport, args.wait):
        rc = build._stream(ctx, cmd, env, args.timeout, f"pkg:{aport}",
                           tracker_cls=progress.PkgTracker, log_prefix="pkg",
                           follow=pmb_log,
                           on_kill=_kill_inside if usable else None)

    # Verify the ARTIFACT, not the exit code. `pmbootstrap build` can write an
    # apk and then fail refreshing the index, and it can also exit 0 having
    # decided there was nothing to do -- neither of which the return code
    # distinguishes. This is already the stated rule for the kernel rungs and
    # it applies identically here.
    landed = want.exists() if want else False
    fresh = landed and (before is False or want.stat().st_mtime != before)
    if rc != 0:
        raise Bail(f"{aport} failed to build", EX_FAIL,
                   f"the log is in {ctx.root / '.run'}")
    if not landed:
        # Ask pmbootstrap why before guessing. It skips a package whose binary
        # is already current, exits 0, and the artifact check alone reads that
        # as a mystery.
        why = why_nothing_built(log_since(pmb_log, log_end), aport)
        if why:
            raise Bail(why[0], EX_FAIL, why[1])
        raise Bail(f"{aport}: build reported success but {want.name} is not "
                   f"there", EX_FAIL, f"looked in {want.parent}")
    ctx.out(ctx.out.paint(
        f"  {want.name}  ({want.stat().st_size // 1024} KiB"
        f"{'' if fresh else ', UNCHANGED -- nothing was rebuilt'})", "green"))
    return EX_OK


def watch_hint(tty: bool) -> str:
    """How to watch this build, said every time a build starts.

    Printed in EVERY mode, not just --detach. The bar goes to whatever
    captured stdout, so when an agent runs the build in a background task the
    developer sees nothing at all -- which is the whole reason this exists.
    Loudest exactly when stdout is not a terminal, because that is when the
    human cannot see the bar.
    """
    if tty:
        return "  porthole pkg watch  -- live bar in another terminal"
    return ("  >>> HUMAN CAN'T SEE THIS BUILD -- stdout is captured, not a "
            "terminal. Tell them to run: porthole pkg watch")


def _arm_ccache(ctx) -> None:
    """Put ccache in chroot_native, where it runs as a native binary.

    tools/ph-build.sh's `_ph_arm_ccache` is what the kernel path already
    calls on every `tkbuild`; PORTHOLE_CCACHE_STANDALONE makes the same
    function reachable without envkernel, which the kernel path needs and
    a package build has no reason to bring up.

    Best effort: a build must never fail because the cache could not be
    armed. Every failure mode here (podman missing, container down, the
    chroot not yet bootstrapped) is caught and swallowed -- an unarmed
    cache just means the next build is slower, not broken.
    """
    import subprocess

    import porthole_cmd_sandbox as sandbox

    try:
        subprocess.run(
            ["podman", "exec", "-e", "PORTHOLE_CCACHE_STANDALONE=1",
             sandbox.CONTAINER, "/bin/bash", "-lc",
             "cd /porthole && source tools/ph-build.sh"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.SubprocessError):
        pass


def _kill_inside() -> None:
    """Kill the build INSIDE the container.

    `podman exec` and the process it exec'd are two different processes:
    killing our client leaves pmbootstrap compiling happily inside, still
    holding the buildroot. Measured -- a killed `porthole pkg build` left
    pmbootstrap running for minutes afterwards, which is precisely the state
    that destroys the next person's build.
    """
    import subprocess

    import porthole_cmd_sandbox as sandbox

    try:
        subprocess.run(
            ["podman", "exec", sandbox.CONTAINER, "sh", "-c",
             "pkill -f 'pmbootstrap.*build' 2>/dev/null; true"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def detach_argv(porthole, aport: str, arch: str, args) -> list:
    """The argv a detached build re-invokes itself with. Pure, so the
    forwarding is testable without spawning anything.

    Rebuilt by hand, so every flag that changes what the build DOES has to be
    listed here or it is silently dropped. Two were: `--force`, which made
    `pkg build X --force --detach` build without force -- the declared flag
    that does nothing, already ruled unacceptable once on this branch -- and
    `--wait`, which made `pkg build X --wait 600 --detach` return EX_OK after
    publish_pending while the child hit the busy-buildroot check with wait=0,
    bailed EX_LOCK into the spawn log, and left `pkg watch` following a
    "running" snapshot for a build that never started.
    """
    argv = [str(porthole), "pkg", "build", aport, "--arch", arch,
            "--timeout", str(args.timeout)]
    if getattr(args, "force", False):
        argv.append("--force")
    if getattr(args, "wait", 0):
        argv += ["--wait", str(args.wait)]
    return argv


def _detach(ctx, args, aport: str, arch: str) -> int:
    """Start the build in its own session and return immediately.

    Re-invokes this same verb rather than duplicating the run path, so a
    detached build is byte-for-byte the build you would have got in the
    foreground -- same tracker, same status file, same log, same artifact
    check. A second implementation is how the background path would quietly
    stop verifying the apk.

    `start_new_session` is the point: the build must survive the terminal, the
    ssh connection and the agent session that started it. A webkit build
    outliving the session that launched it is the normal case, not the edge
    one.
    """
    import subprocess

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    rundir.mkdir(parents=True, exist_ok=True)
    spawn_log = rundir / f"pkg-{aport}-detached.log"
    argv = detach_argv(ctx.root / "bin" / "porthole", aport, arch, args)
    with open(spawn_log, "w") as handle:
        proc = subprocess.Popen(argv, cwd=str(ctx.root), stdout=handle,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    # Claim the status file for THIS run before returning, or a `watch` that
    # follows in the next second reads the previous build's final snapshot and
    # reports it as the answer.
    import porthole_progress as progress

    progress.publish_pending(rundir, f"pkg:{aport}", proc.pid)
    ctx.out.kv("pid", str(proc.pid), 10)
    ctx.out.kv("log", str(spawn_log), 10)
    tty = sys.stdout.isatty()
    ctx.out(ctx.out.paint(watch_hint(tty), "cyan" if tty else "yellow"))
    ctx.out(ctx.out.paint("  porthole pkg status --json  # one-shot, for a "
                          "script or an agent", "cyan"))
    return EX_OK


# Kept as names because tests/test_pkg.py calls them directly (the pattern
# `_pmb_workdir` already uses for `porthole_cmd_brief`): the implementation
# moved to `porthole_progress` in Task 13, so `porthole build watch` (Task 14)
# can share one `watch()` loop instead of growing a second one that drifts.
def wait_ceiling(tty: bool, now: float, seconds: float = 30.0):
    import porthole_progress as progress

    return progress.wait_ceiling(tty, now, seconds)


def waiting_line(snap, now=None) -> str:
    import porthole_progress as progress

    return progress.waiting_line(snap, now)


def _watch(ctx, args) -> int:
    """Follow the status file until the build stops.

    Thin on purpose: the loop itself -- the no-ceiling-on-a-tty rule, the
    never-silent waiting line, the stale-run check, the poll-not-sleep -- now
    lives in `porthole_progress.watch`, shared with `porthole build watch`.
    """
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))

    # A raw sink, not `ctx.out`: `progress.watch` bakes its own cursor
    # handling into every string it emits (the walk back up the repainted
    # block on a tty, a trailing `\n` otherwise), so writing exactly what it
    # is given -- no `ctx.out`'s automatic newline -- is what keeps the bar
    # redrawing in place on a terminal exactly as it did before the move.
    def out(line):
        sys.stdout.write(line)
        sys.stdout.flush()

    # Same probe `_build` uses to refuse to start on top of a foreign build:
    # one that came through `sandbox shell --command` publishes no status, and
    # without this `watch` reports the previous run as though it were the news.
    #
    # A LAMBDA, and not `_workspace_usable` first: `progress.watch` asks only
    # when there is nothing live to follow, so a healthy watch pays no podman
    # at all -- computing the argument up front put half a second of `podman
    # ps` plus `inspect` on every invocation, including the ones that attach
    # to a running build immediately. And the question here is "is anything
    # building in the workspace", not "is the workspace wired for THIS
    # device": a container built for another phone still owns the buildroot
    # and still publishes nothing, so `running_build` asks it either way.
    return progress.watch(rundir, "pkg-status.json", args.interval, out,
                          start_hint="start one with "
                                     "`porthole pkg build <aport>`",
                          probe=lambda: running_build(ctx, True))


def _outdated(ctx) -> int:
    """Which local aports no longer match the .apk that was built from them."""
    pmaports = _find_pmaports(ctx)
    arch = ctx.cfg.get("PORTHOLE_ARCH") or "aarch64"
    usable, _ = __import__("porthole_cmd_build")._workspace_usable(ctx)
    stale = outdated(pmaports, _packages_dir(ctx, usable), arch)

    def render():
        if not stale:
            ctx.out("every locally built aport matches its apk")
            return
        ctx.out.heading(f"{len(stale)} aport(s) need rebuilding")
        for name, why in stale:
            ctx.out.kv(name, why, 22)
        ctx.out.blank()
        ctx.out(ctx.out.paint(
            f"  porthole pkg build {stale[0][0]}", "cyan"))

    return ctx.emit([{"aport": n, "why": w} for n, w in stale], render)


def _status(ctx) -> int:
    """Where the package build is -- the same contract `porthole build status`
    publishes, so an agent polls one shape for both."""
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    try:
        snap = json.loads((rundir / "pkg-status.json").read_text())
    except (OSError, ValueError):
        return ctx.emit({"state": "none"},
                        lambda: ctx.out("no package build has run in this "
                                        "checkout"))

    def render():
        head, rows = progress.status_report(snap)
        ctx.out("  " + head)
        for label, value in rows:
            ctx.out.kv(label, value, 10)

    return ctx.emit(snap, render)


def stop_plan(snap, alive=None) -> tuple:
    """`("kill", pid)` or `("none", 0)`. Pure, so the decision is testable.

    `state: running` is not evidence that anything is running -- a SIGKILLed
    build, a reboot, a terminal that went away all leave that field set with
    no one to clear it, and the pid in it is a number the OS has since been
    free to hand to something else. progress.liveness() exists for exactly
    that, and trusting the field instead is how this could signal an unrelated
    process. `alive` is a parameter rather than a lookup inside so the
    decision stays pure and testable with no clock and no /proc.
    """
    import porthole_progress as progress

    if progress.liveness(snap, alive) != "running":
        return ("none", 0)
    pid = (snap or {}).get("pid")
    return ("kill", pid) if isinstance(pid, int) and pid > 0 else ("none", 0)


def _stop(ctx) -> int:
    """Cancel a running package build, both sides of the container boundary.

    Killing the `podman exec` client does NOT kill what it exec'd: the build
    keeps compiling inside, still holding the buildroot, and the next build
    then deletes its source tree. Cancelling used to mean doing both by hand.
    """
    import signal

    import porthole_buildroot as buildroot
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    try:
        snap = json.loads((rundir / "pkg-status.json").read_text())
    except (OSError, ValueError):
        snap = {}

    action, pid = stop_plan(snap)
    if action == "kill":
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    # Always sweep the container side: a build started outside this verb holds
    # the buildroot just as hard, and that is how the collisions happened.
    _kill_inside()

    workdir = _pmb_workdir(ctx, build_module()._workspace_usable(ctx)[0])
    holder = buildroot.lock_holder(workdir)
    # Only UPDATE a snapshot; never invent one. With no status file at all
    # snap is {}, and writing it back published `{"state": "failed"}` -- a
    # failed build that never existed, which `pkg status` and `brief` then
    # reported as the answer.
    if snap:
        snap["state"] = "failed"
        try:
            (rundir / "pkg-status.json").write_text(json.dumps(snap, indent=2))
        except OSError:
            pass

    def render():
        if action == "kill":
            ctx.out(ctx.out.paint(f"  stopped pid {pid}", "green"))
        else:
            ctx.out("no package build was running here")
        ctx.out(ctx.out.paint("  container-side pmbootstrap swept", "grey"))
        if holder:
            ctx.out(ctx.out.paint(f"  lock was held by: {holder}", "grey"))
        _ = progress  # rendering only

    return ctx.emit({"stopped": pid if action == "kill" else None}, render)


def _fork_names(ctx, pmaports) -> set:
    """Which pmaports packages this branch has actually changed."""
    import porthole_cmd_aports as aports

    try:
        return {path.rsplit("/", 1)[-1]
                for path in aports.local_forks(ctx, pmaports)}
    except Bail:
        return set()


def _search(ctx, args) -> int:
    """What is buildable, and out of which tree.

    Nothing answered this before. `porthole aports` lists the packages named
    after your device, which is a much smaller question and was the only one
    on offer -- so a name that was not a kernel or a device package looked
    like a name that did not exist.
    """
    import porthole_pmaports as pmap

    text = args.target
    if not text:
        raise Bail("search for what?", EX_USAGE,
                   "porthole pkg search calculator")

    pmaports = _find_pmaports(ctx)
    upstream = pmap.find_aports_upstream(pmaports)
    hits, guessed = search(pmaports, upstream, text)
    forks = _fork_names(ctx, pmaports) if hits else set()

    shown = hits[:LIST_CAP]
    for hit in shown:
        hit["version"] = apkbuild_version(hit["path"])
        hit["state"] = ("forked" if hit["name"] in forks
                        else "buildable" if hit["tree"] == "pmaports"
                        else "needs a fork")

    payload = {"query": text, "matched": len(hits), "guessed": guessed,
               "alpine_tree": str(upstream) if upstream else "",
               "packages": shown}

    def render():
        o = ctx.out
        if not shown:
            o(f"nothing like {text!r} in either aports tree.")
            if not upstream:
                o.blank()
                o.hint("Alpine's aports checkout is missing, so only pmaports "
                       "was searched — `pmbootstrap pull` clones it")
            return
        if guessed:
            o(o.paint(f"nothing contains {text!r}. The closest names:",
                      "yellow"))
            o.blank()

        name_w = max(len(h["name"]) for h in shown)
        where_w = max(len(h["where"]) for h in shown)
        for tree, title in (
                ("pmaports", "pmaports — buildable now"),
                ("alpine", "alpine — fork it before you can build it")):
            rows = [h for h in shown if h["tree"] == tree]
            if not rows:
                continue
            o.heading(title)
            for hit in rows:
                mine = (o.paint("   yours", "green")
                        if hit["state"] == "forked" else "")
                o(f"  {hit['name']:<{name_w}}  "
                  f"{o.paint(hit['where'].ljust(where_w), 'grey')}  "
                  f"{hit['version']}{mine}")
            o.blank()
        if len(hits) > len(shown):
            o(f"  ... and {len(hits) - len(shown)} more; narrow the search.")
            o.blank()

        first = shown[0]
        if first["tree"] == "alpine":
            o.hint(f"porthole pkg fork {first['name']} --yes   "
                   f"copy it into pmaports, where a build can see it")
        else:
            o.hint(f"porthole pkg build {first['name']}")

    return ctx.emit(payload, render)


def _fork(ctx, args) -> int:
    """Copy an Alpine aport into pmaports, which is what makes it buildable.

    The rung that was missing between finding a package and building one.
    `pmbootstrap build` reads pmaports and nothing else, so every package in
    Alpine's tree -- phosh, gnome-calculator, gstreamer -- was a dead end
    whose only exit was to reach past porthole to pmbootstrap, which
    AGENTS.md section 1 tells you not to do. temp/gst-plugins-good and
    temp/webkit2gtk-6.0 both got here that way.

    It takes no buildroot lock, deliberately: aportgen writes into pmaports
    and refreshes an APKINDEX. It never touches a chroot's build directory,
    so holding the mutex would only block builds that have nothing to fear.
    """
    import porthole_cmd_aports as aports
    import porthole_pmaports as pmap

    name = args.target
    if not name:
        raise Bail("fork what?", EX_USAGE,
                   "porthole pkg fork gnome-calculator --yes")

    pmaports = _find_pmaports(ctx)
    existing = find_aport(pmaports, name)
    if existing:
        ctx.out(f"{name} is already in pmaports, at "
                f"{existing.relative_to(pmaports)}")
        ctx.out.hint(f"porthole pkg build {name}")
        return EX_OK

    upstream = pmap.find_aports_upstream(pmaports)
    if not upstream:
        # 69: there is nothing here that could fork, which is not a statement
        # about the package.
        raise Bail("Alpine's aports checkout is not beside pmaports",
                   EX_UNAVAILABLE,
                   "`pmbootstrap pull` clones it into the same cache_git/")

    hits = sorted(upstream.glob(f"*/{name}"))
    if not hits:
        message, hint = missing_aport_hint(pmaports, upstream, name)
        raise Bail(message, EX_FAIL, hint)

    ctx.out.kv("package", f"{name}   ({hits[0].parent.name}/)", 9)
    ctx.out.kv("from", str(upstream), 9)
    ctx.out.kv("into", f"{pmaports}/temp/", 9)
    if not args.yes:
        ctx.out.blank()
        ctx.out.hint(f"porthole pkg fork {name} --yes   to actually do it")
        return EX_OK

    usable, why_not = build_module()._workspace_usable(ctx)
    if usable and not container_has_upstream():
        # 69, not 1: nothing here could fork, which is not a statement about
        # the package. Checked BEFORE starting, because the alternative is
        # what actually happened -- aportgen decided the tree was missing and
        # began a fresh 784 MB clone of Alpine's aports from GitLab.
        raise Bail("the workspace cannot see Alpine's aports",
                   EX_UNAVAILABLE,
                   f"the host has it at {upstream}, but the container mounts "
                   f"pmaports and nothing else, so aportgen would clone 784 "
                   f"MB rather than read it. That mount is a change to the "
                   f"isolation boundary (porthole_cmd_sandbox._mounts), which "
                   f"is your decision to make and not this verb's")
    if usable:
        ctx.out(ctx.out.paint("  forking IN THE WORKSPACE (container)",
                              "cyan"))
        cmd = fork_cmd(name)
        ctx.out(ctx.out.paint(f"  $ {' '.join(cmd)}", "grey"))
        try:
            rc = subprocess.run(cmd, timeout=900).returncode
        except subprocess.TimeoutExpired:
            raise Bail(f"aportgen timed out forking {name}", EX_FAIL) from None
    else:
        ctx.out(ctx.out.paint(f"  forking ON THE HOST ({why_not})", "cyan"))
        rc, _, _ = aports.pmb(ctx, "aportgen", "--fork-alpine", name,
                              timeout=900)
    if rc != 0:
        # NAMES NO CAUSE. The first version of this line asserted "a
        # subpackage cannot be forked on its own", and the very first real
        # fork attempted -- gnome-calculator -- failed for something else
        # entirely: pmbootstrap could not parse a `devhelp` subpackage split
        # that abuild has no default implementation for. A confident wrong
        # diagnosis is worse than none, because it sends the reader to fix a
        # package that was never the problem. aportgen's own error is on the
        # screen directly above this; AGENTS.md section 6.
        raise Bail(f"aportgen could not fork {name}", EX_FAIL,
                   "its error is printed above, and `pmbootstrap log` has "
                   "the rest. Two causes are common: the name is a "
                   "subpackage rather than an origin package, or "
                   "pmbootstrap cannot parse that particular APKBUILD")

    # aportgen exiting 0 is not proof it wrote anything, and a fork that
    # silently did not land reads downstream as "the build cannot find it".
    landed = find_aport(pmaports, name)
    if not landed:
        raise Bail(f"aportgen exited 0 but {name} is not in pmaports",
                   EX_FAIL, "porthole aports status   to see what it did")

    ctx.out.kv("landed", str(landed.relative_to(pmaports)), 9)
    ctx.out.hint(f"porthole pkg build {name} --detach")
    ctx.out.hint("porthole pkg watch")
    return EX_OK


def build_module():
    import porthole_cmd_build as build

    return build


def cmd_pkg(args, ctx) -> int:
    action = args.action or "status"
    if action == "status":
        return _status(ctx)
    if action == "watch":
        return _watch(ctx, args)
    if action == "outdated":
        return _outdated(ctx)
    if action == "stop":
        return _stop(ctx)
    if action == "search":
        return _search(ctx, args)
    if action == "fork":
        return _fork(ctx, args)
    return _build(ctx, args)


SPEC = {
    "verb": "pkg",
    "order": 21,
    "help": "find, fork and build a userspace aport, with a real progress bar",
    "description": (
        "`porthole build` is the kernel loop; every rung of it produces a\n"
        "kernel artifact. This is the other half: a userspace aport, built\n"
        "through pmbootstrap in the workspace, reporting through the same\n"
        "tracker, status file and log that the kernel rungs already use.\n\n"
        "The percentage here is REAL. ninja states its own total, so unlike\n"
        "a kernel build nothing has to be inferred from a previous run.\n\n"
        "WATCHING ONE COSTS NOTHING. `build --detach` returns immediately and\n"
        "the build outlives the session; `watch` follows it with a live bar in\n"
        "any other terminal, and `status --json` is the one-shot an agent\n"
        "reads instead of polling. Nobody has to sit on the output.\n\n"
        "TWO TREES, AND ONLY ONE OF THEM BUILDS. pmbootstrap keeps pmaports\n"
        "and Alpine's aports side by side, and `pmbootstrap build` reads\n"
        "pmaports only -- so Alpine's twelve thousand packages are present,\n"
        "useful, and unbuildable until `fork` copies one across. `search`\n"
        "looks in both and says which tree a name is in, which is the actual\n"
        "answer to \"why does my build say the package does not exist\".\n\n"
        "See docs/HANDOFF-package-builds.md."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["build", "search", "fork", "status",
                                  "watch", "outdated", "stop"],
                      "help": "build | search | fork | status | watch | "
                              "outdated | stop"}),
        (["target"], {"nargs": "?", "metavar": "APORT",
                      "help": "build/fork: the aport. search: text to look for"}),
        (["--arch"], {"metavar": "ARCH",
                      "help": "build: target architecture (default: the profile's)"}),
        (["--timeout"], {"type": int, "default": DEFAULT_TIMEOUT,
                         "help": f"build: seconds before giving up "
                                 f"(default {DEFAULT_TIMEOUT})"}),
        (["--verbose"], {"action": "store_true",
                         "help": "build: stream the raw output"}),
        (["--dry-run"], {"action": "store_true",
                         "help": "build: print the command and stop"}),
        (["--detach"], {"action": "store_true",
                        "help": "build: start it in its own session and return"}),
        (["--force"], {"action": "store_true",
                       "help": "build: rebuild even if the apk is current"}),
        (["--interval"], {"type": float, "default": 1.0,
                          "help": "watch: seconds between reads (default 1)"}),
        (["--wait"], {"type": float, "default": 0.0, "metavar": "SECONDS",
                      "help": "build: queue this long for the buildroot "
                              "instead of refusing"}),
        (["--yes"], {"action": "store_true",
                     "help": "fork: actually write into pmaports"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "escapes_scope": True,
    "run": cmd_pkg,
    "examples": [
        "porthole pkg search calculator",
        "porthole pkg fork gnome-calculator --yes",
        "porthole pkg build phoc",
        "porthole pkg build webkit2gtk-6.0 --detach",
        "porthole pkg watch",
        "porthole pkg outdated",
        "porthole pkg status --json",
        "porthole pkg stop",
    ],
}
