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
import tempfile
import time
from typing import NamedTuple

from porthole_cli import (Bail, EX_FAIL, EX_LOCK, EX_OK, EX_STATE,
                          EX_UNAVAILABLE, EX_USAGE, child_env)

# Four hours. webkit is the reason: it is measured in hours on this hardware,
# and a timeout that kills it at the default half hour would be a tool that
# only works on packages nobody needed help with.
DEFAULT_TIMEOUT = 4 * 60 * 60

# Where an aport can live. Two levels because device aports are nested
# (device/testing/<name>) and everything else is not (temp/<name>,
# main/<name>). Same lookup ph-pkgcheck.sh does, for the same reason: which
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


def subpackage_names(text: str) -> list:
    """The names in an APKBUILD's `subpackages=`, `$pkgname` expanded. Pure.

    A name that still holds a `$` is dropped rather than returned literally,
    for the reason `apkbuild_fields` gives: everything downstream matches it
    against a package name, and a wrong one reports a package that exists as
    absent.
    """
    assignments = {k: v.strip() for k, v in _ASSIGN.findall(text)}
    out = []
    for body in _bodies(text, "subpackages"):
        for token in body.split():
            name = expand_vars(token.split(":", 1)[0], assignments).strip()
            if name and "$" not in name:
                out.append(name)
    return out


def find_subpackage(pmaports, name):
    """(aport directory, its pkgname) for a SUBPACKAGE name, or (None, "").

    A subpackage is the normal way to ship something optional -- a
    proprietary TrustZone blob kept out of the device package's dependencies
    so that it is installed explicitly or not at all -- which is exactly the
    case where `pkg install <the-thing>` has to work. It is also the name
    the user knows, because it is the name apk uses. Resolving only top-level
    directory names made every one of them unreachable (#107).

    ponytail: walks the name's own `-` segments rather than indexing every
    subpackage in the tree. `firmware-google-taimen-fingerprint` is built by
    `firmware-google-taimen`, and prefixing a subpackage with its origin is
    abuild's own convention -- so this costs a handful of globs on a path
    that was about to fail anyway, instead of reading 14,000 APKBUILDs. A
    subpackage that does NOT carry its parent's prefix (`py3-foo` from `foo`)
    is still a miss; scan the tree here if one ever costs more than the scan.
    """
    parts = name.split("-")
    for cut in range(len(parts) - 1, 0, -1):
        parent = "-".join(parts[:cut])
        directory = find_aport(pmaports, parent)
        if directory is None:
            continue
        try:
            text = (directory / "APKBUILD").read_text(errors="replace")
        except OSError:
            continue
        if name in subpackage_names(text):
            return directory, parent
    return None, ""


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
    directory, parent = find_subpackage(pmaports, name)
    if directory is not None:
        # "no aport named X" is not merely unhelpful here, it is untrue: the
        # package exists and is built. Name what provides it.
        return (f"{name} is a subpackage of {parent}, not an aport of its own",
                f"porthole pkg install {name}   installs it; "
                f"`porthole pkg build {parent}` builds it")
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


def _bodies(text: str, var: str):
    """Every `var=` assignment body in the file, quotes resolved.

    A dependency list is usually several quoted lines, so the body has to be
    taken to its closing quote rather than to the end of the line -- and
    `$makedepends` is normally the FIRST of those lines rather than the first
    token after the `=`. That is the shape Alpine's mesa uses, and reading
    only the `=` line saw none of the five packages it hides there.
    """
    for match in re.finditer(rf'(?m)^[ \t]*{re.escape(var)}=(.*)$', text):
        rest = match.group(1)
        if rest[:1] in ('"', "'"):
            end = text.find(rest[0], match.start(1) + 1)
            yield text[match.start(1) + 1:end] if end != -1 else rest
        else:
            yield rest.strip('"\'')


def bare_dep(dep: str) -> str:
    """`libclc-dev~22` -> `libclc-dev`. An Alpine version constraint.

    Compared bare on both sides: an append that pins a version is still
    installed by the plain name in the static list, and warning about that is
    warning about correct code.
    """
    return re.split(r"[~=<>]", dep.lstrip("!"), maxsplit=1)[0]


def static_deps(text: str, var: str) -> set:
    """Packages assigned to `var` OUTRIGHT -- the ones pmbootstrap can see.

    An append (`var="$var ..."`) is deliberately excluded: that is the form
    this whole check is about.
    """
    return {bare_dep(tok)
            for body in _bodies(text, var)
            if not body.lstrip().startswith("$" + var)
            for tok in body.split() if not tok.startswith("$")}


def appended_deps(text: str, var: str) -> list:
    """Packages appended to `var`, in file order. The invisible ones."""
    found = []
    # `$makedepends` or `${makedepends}`, but not `$makedepends_build`: the
    # Alpine split `makedepends="$makedepends_build $makedepends_host"` names
    # two other variables, not an append with a package called `_build`.
    own = re.compile(rf"\$(?:{re.escape(var)}(?![A-Za-z0-9_])|\{{{re.escape(var)}\}})")
    for body in _bodies(text, var):
        head = body.lstrip()
        match = own.match(head)
        if match:
            found += head[match.end():].split()
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
    assignments = {k: v.strip() for k, v in _ASSIGN.findall(text)}
    missing = []
    for var in _DEP_VARS:
        visible = static_deps(text, var)
        for tok in appended_deps(text, var):
            if tok.startswith("$"):
                continue
            # `clang$_llvmver-dev` names no package anyone can act on.
            dep = expand_vars(tok, assignments)
            if bare_dep(dep) not in visible:
                missing.append(dep)
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
      - its CONTENT changed since the apk was built         (you edited it)

    The second is the one that bit: temp/phoc sat at 0.56.0 while the mirror
    moved on, apk installed the newer stock build, and both GPU-reset patches
    vanished with no message anywhere.

    "Content changed" is asked of git, not of mtime. mtime was the first
    implementation and it cried wolf: pmaports is a branch-switched checkout,
    and every `git checkout` restamps every file it touches without changing a
    byte. On 2026-09-09 that reported 13 aports as needing a rebuild when all
    13 already had their exact pkgver-pkgrel sitting in the repo -- an alarm
    with a 100% false positive rate, which is an alarm nobody reads, which is
    how the phoc incident happens again.
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
        changed_at, how = aport_changed_at(directory)
        if changed_at > apk_at:
            found.append((name, "{} since it was last built".format(how)))
    return found


def aport_changed_at(directory) -> tuple:
    """(when this aport's content last changed, how we know it).

    git first, because it is the only source that distinguishes a change from a
    checkout. An uncommitted edit is the loudest case and is reported as now,
    so it always beats the apk. Otherwise the last commit that touched this
    directory is when its content actually moved -- restamping every file, as
    a branch switch does, does not move it.

    Falls back to mtime when git cannot answer (not a checkout, no git on the
    host, a bare export). That is the old behaviour, kept deliberately: it
    over-reports, and over-reporting a rebuild costs minutes while
    under-reporting one ships a phone without a patch.
    """
    import subprocess
    import time

    d = str(directory)
    try:
        dirty = subprocess.run(["git", "-C", d, "status", "--porcelain", "--", d],
                               capture_output=True, text=True, timeout=10)
        if dirty.returncode == 0:
            if dirty.stdout.strip():
                return time.time(), "edited (uncommitted)"
            log = subprocess.run(
                ["git", "-C", d, "log", "-1", "--format=%ct", "--", d],
                capture_output=True, text=True, timeout=10)
            stamp = (log.stdout or "").strip()
            if log.returncode == 0 and stamp.isdigit():
                return float(stamp), "committed"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    newest = max((f.stat().st_mtime for f in pathlib.Path(d).iterdir()
                  if f.is_file()), default=0.0)
    return newest, "edited"


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


# `abuild build rootpkg update_abuildrepo_index` -- the three actions a resume
# runs, and the exact line that was typed by hand twice on 2026-09-02 against a
# 5.5-hour webkit2gtk-6.0 tree. `build` recompiles only what changed, `rootpkg`
# re-splits the subpackages and makes the apks as fakeroot, and the index
# refresh is what makes the result installable from the local repo. Any abuild
# function name is a valid action, which is what --actions is for.
RESUME_ACTIONS = "build rootpkg update_abuildrepo_index"

# Architectures whose builds run under linux32, copied from pmbootstrap's own
# `Arch.linux32_required` -- without it uname says aarch64 inside an armv7
# chroot and configure scripts pick the wrong ABI.
LINUX32 = ("armhf", "armv7", "x86")


# Where pmbootstrap bind-mounts the target chroot for a cross-native2 build,
# and the arch facts it derives from it (pmb/core/arch.py: alpine_triple()
# and go()). Only the arches a port actually cross-builds for.
SYSROOT = "/mnt/sysroot"
CROSS_ARCH = {
    "aarch64": ("aarch64-alpine-linux-musl", "arm64"),
    "armhf": ("armv6-alpine-linux-musleabihf", "arm"),
    "armv7": ("armv7-alpine-linux-musleabihf", "arm"),
    "riscv64": ("riscv64-alpine-linux-musl", "riscv64"),
    "x86": ("i586-alpine-linux-musl", "386"),
    "x86_64": ("x86_64-alpine-linux-musl", "amd64"),
}


def cross_native2(text: str) -> bool:
    """Whether pmbootstrap builds this APKBUILD with cross-native2. Pure.

    pmb/build/autodetect.py decides it from `options`, and it moves the whole
    build: the tree is in the NATIVE chroot and abuild runs there with the
    target chroot as its sysroot, so a resume that looked in the buildroot
    would find somebody else's tree or none.
    """
    return any("pmb:cross-native2" in body.split()
               for body in _bodies(text, "options"))


def abuild_env(arch: str, native2: bool = False) -> dict:
    """What pmbootstrap exports before it runs abuild (pmb/build/backend.py).

    Not "what abuild happens to need": SUDO_APK is how abuild installs into
    the buildroot with no root at all, and CARCH is what makes it produce a
    package for the target rather than for whatever qemu is emulating on.

    cross-native2 replaces CARCH with CHOST and CBUILDROOT, from which abuild
    derives the cross compiler, --sysroot and pkg-config's sysroot, plus the
    Go and Rust (cargo, bindgen) settings pmbootstrap adds for that mode.
    """
    env = {"SUDO_APK": "abuild-apk --no-progress"}
    if not native2:
        env["CARCH"] = arch
        return env
    if arch not in CROSS_ARCH:
        raise Bail(f"cannot resume a cross-native2 build for {arch}", EX_FAIL,
                   f"known: {', '.join(sorted(CROSS_ARCH))}")
    triple, goarch = CROSS_ARCH[arch]
    env.update({
        "PMB_CROSS": "cross-native2",
        "CHOST": arch,
        "CBUILDROOT": SYSROOT,
        "CFLAGS": f"-Wl,-rpath-link={SYSROOT}/usr/lib",
        "CGO_CFLAGS": f"--sysroot={SYSROOT}",
        "CGO_LDFLAGS": f"--sysroot={SYSROOT}",
        "GOARCH": goarch,
        "CARGO_BUILD_TARGET": triple,
        f"CARGO_TARGET_{triple.upper().replace('-', '_')}_LINKER":
            f"{triple}-gcc",
        "RUSTFLAGS": f"--sysroot={SYSROOT}/usr -Clink-arg=--sysroot={SYSROOT}",
        f"BINDGEN_EXTRA_CLANG_ARGS_{triple.replace('-', '_')}":
            f"--target={triple} --sysroot={SYSROOT}",
    })
    return env


def sysroot_mount(arch: str) -> str:
    """The shell line that gives a cross-native2 resume its sysroot. Pure.

    pmbootstrap bind-mounts the target chroot at /mnt/sysroot for the build
    and unmounts it when it exits, so the tree a failed build leaves behind
    has no sysroot under it. `pmbootstrap chroot` does not mount one; root in
    the workspace container can. Idempotent, and pmbootstrap's own bind
    replaces it on the next build (pmb/helpers/mount.py bind(umount=True)).
    """
    target = f"/pmb/chroot_native{SYSROOT}"
    return (f"mkdir -p {target} && {{ mountpoint -q {target} || "
            f"mount --bind /pmb/chroot_buildroot_{shlex.quote(arch)} {target}; }}")


def resume_line(arch: str, actions: str = RESUME_ACTIONS,
                patches: bool = False, pkgrel=None,
                native2: bool = False) -> str:
    """The one shell line a resume runs inside the buildroot. Pure, so every
    gotcha it encodes is a test rather than another hour on a device."""
    env = " ".join("%s=%s" % (k, shlex.quote(v))
                   for k, v in sorted(abuild_env(arch, native2).items()))
    steps = ["cd /home/pmos/build"]
    if pkgrel is not None:
        # The BUILD COPY of the APKBUILD is the one abuild reads; bumping only
        # the aport produces an apk with the old -rN and a repo that then says
        # nothing changed. (The aport is bumped separately, on the host.)
        steps.append("sed -i 's/^pkgrel=.*/pkgrel=%d/' APKBUILD" % pkgrel)
    if patches:
        # `prepare` is exactly what a resume skips, and applying the patches is
        # all `prepare` was doing that matters here. $builddir is abuild's and
        # only the APKBUILD knows it, so source it the way abuild does. `-N`
        # makes an already-applied patch a skip instead of a failure, which is
        # what lets this be re-run.
        steps.append('srcdir=/home/pmos/build/src; . ./APKBUILD; '
                     'b=${builddir:-$srcdir/$pkgname-$pkgver}; '
                     'for p in *.patch; do [ -e "$p" ] || continue; '
                     'echo ">>> patch $p"; patch -N -p1 -d "$b" -i "$PWD/$p"; '
                     'done; true')
    # A pkg/ left by an earlier rootpkg makes the -lang split fail with "file
    # already exists" -- after the compile, which is the expensive place to
    # find out. rootpkg recreates it.
    steps.append("rm -rf pkg")
    abuild = "%s abuild -d -D postmarketOS %s" % (env, actions)
    steps.append(("linux32 " + abuild) if arch in LINUX32 else abuild)
    return " && ".join(steps)


def resume_cmd(arch: str, line: str, native2: bool = False) -> list[str]:
    """`pmbootstrap chroot` into the buildroot, as the build user.

    --output log and not the default: the default hands the terminal to the
    child, and this output has to reach the tracker through pmbootstrap's
    log.txt the same way `pkg build`'s does. -b names the BUILDROOT chroot --
    without it this would run in the native one, where the tree is not.
    A cross-native2 tree IS in the native one, so there it is left out.
    """
    where = [] if native2 else ["-b", arch]
    return (["pmbootstrap", "chroot", "--output", "log"] + where
            + ["--user", "--", "sh", "-c", line])


def in_container(call: str, stdin: bool = False) -> list[str]:
    """Wrap one pmbootstrap command line for the workspace container.

    `stdin` attaches ours to the child (podman exec -i). Only the patch copy
    below needs it, and it is off by default: a build reading from a closed
    stdin is the behaviour every other caller here already relies on.

    PYTHONUNBUFFERED is not a nicety, it is what makes `pkg build` work at
    all. pmbootstrap is Python; writing to a pipe rather than a tty it
    switches to block buffering and holds its output until it exits.
    Measured: five minutes into a gst-plugins-good build, with cc and lto1
    visibly running inside the container, the log file was ZERO BYTES and
    the bar had never moved. Every line arrives at the end, which is exactly
    the black box this was built to replace.
    """
    import porthole_cmd_sandbox as sandbox

    return (["podman", "exec"] + (["-i"] if stdin else [])
            + ["-e", "PYTHONUNBUFFERED=1", sandbox.CONTAINER,
               "/bin/bash", "-lc", f"cd /porthole && {call}"])


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
            pmaports, pmap.find_aports_upstream(pmaports, ctx.cfg), aport)
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
    env = child_env(os.environ, ctx.cfg)
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
        if needs_buildroot_sccache(text):
            _arm_buildroot_sccache(arch)

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


def _put_in_tree(ctx, usable: bool, arch: str, local: pathlib.Path,
                 native2: bool = False) -> int:
    """Copy one file from the aport into the buildroot's build tree.

    Through `cat` in the chroot rather than a host copy: the tree belongs to
    the chroot's build user, which on the host is a subuid nobody here can
    write as, and inside the workspace container it is root that can. One
    route that works in both, and the file lands owned by the user abuild
    runs as, which is what a host copy gets wrong even when it is permitted.
    """
    where = [] if native2 else ["-b", arch]
    cmd = (["pmbootstrap", "chroot", "--output", "interactive"] + where
           + ["--user", "--", "sh", "-c",
              "cat > /home/pmos/build/%s" % shlex.quote(local.name)])
    if usable:
        cmd = in_container(" ".join(shlex.quote(a) for a in cmd), stdin=True)
    with open(local, "rb") as handle:
        return subprocess.run(cmd, stdin=handle).returncode


def tree_holds(tree: pathlib.Path, aport: str) -> str:
    """The pkgname the buildroot's tree belongs to, "" if there is no tree.

    One buildroot, one tree: whatever built last owns it. Resuming without
    asking would run abuild over somebody else's half-built source and blame
    the compiler, which is the exact failure two-pmbootstrap-builds-destroy-
    each-other is about. Pure, so the check is a test.
    """
    try:
        text = (tree / "APKBUILD").read_text(errors="replace")
    except OSError:
        return ""
    return apkbuild_fields(text).get("pkgname", "") or aport


def _resume(ctx, args) -> int:
    """Resume a package build from the tree the last one left behind.

    WHY THIS IS NOT `pkg build`
        `pmbootstrap build` runs abuild's whole sequence -- clean, fetch,
        unpack, prepare, build, check, rootpkg -- and copy_to_buildpath
        DELETES /home/pmos/build first. Against a 5.5-hour webkit2gtk-6.0
        tree, "recompile three files and repackage" therefore costs 5.5
        hours. That was done by hand twice on 2026-09-02 (porthole-dev/
        porthole#46), and every gotcha it cost is encoded in resume_line()
        and in the guards below.
    """
    import porthole_cmd_build as build
    import porthole_progress as progress

    aport = args.target
    if not aport:
        raise Bail("which aport?", EX_FAIL,
                   "porthole pkg resume <aport>, e.g. "
                   "`porthole pkg resume webkit2gtk-6.0`")

    arch = args.arch or ctx.cfg.get("PORTHOLE_ARCH") or "aarch64"
    pmaports = _find_pmaports(ctx)
    directory = find_aport(pmaports, aport)
    if directory is None:
        import porthole_pmaports as pmap

        message, hint = missing_aport_hint(
            pmaports, pmap.find_aports_upstream(pmaports, ctx.cfg), aport)
        raise Bail(message, EX_FAIL, hint)

    usable, why_not = build._workspace_usable(ctx)
    if not usable and not shutil.which("pmbootstrap"):
        raise Bail(f"no workspace and no pmbootstrap on PATH ({why_not})",
                   EX_UNAVAILABLE, "run `porthole sandbox up` first")
    workdir = _pmb_workdir(ctx, usable)
    native2 = cross_native2(
        (directory / "APKBUILD").read_text(errors="replace"))
    if native2 and not usable:
        raise Bail(f"{aport} builds with cross-native2, which needs its "
                   "sysroot mounted", EX_UNAVAILABLE,
                   "the workspace can mount it: `porthole sandbox up`")
    chroot = "chroot_native" if native2 else f"chroot_buildroot_{arch}"
    tree = workdir / chroot / "home" / "pmos" / "build"

    holder = tree_holds(tree, aport)
    if not holder:
        raise Bail(f"there is no build tree in {tree}", EX_FAIL,
                   f"a resume needs the tree a previous build left behind; "
                   f"`porthole pkg build {aport}` makes one from scratch")
    if holder != aport:
        raise Bail(f"the buildroot's tree is {holder}, not {aport}", EX_FAIL,
                   f"resuming would run abuild over {holder}'s source. "
                   f"`porthole pkg build {aport}` starts a fresh tree")

    if not args.wait and not is_free(workdir):
        raise Bail(f"the buildroot is busy: "
                   f"{lock_holder(workdir) or 'another build'}", EX_LOCK,
                   "two pmbootstrap builds share one buildroot and delete "
                   "each other's source tree. Wait, or --wait SECONDS.")
    foreign = running_build(ctx, usable)
    if foreign:
        raise Bail(f"a pmbootstrap build is already running: {foreign}",
                   EX_LOCK,
                   "it holds no lock (started outside `porthole pkg`), but it "
                   "owns the buildroot all the same -- resuming now runs "
                   "abuild over a tree it is still writing")

    # The BUILD copy is what abuild reads, so it is also what says which apk
    # to expect. --pkgrel is applied to it inside the chroot below.
    fields = apkbuild_fields((tree / "APKBUILD").read_text(errors="replace"))
    if args.pkgrel is not None:
        fields["pkgrel"] = str(args.pkgrel)
    ctx.out.kv("aport", str(directory.relative_to(pmaports)), 10)
    ctx.out.kv("tree", str(tree), 10)
    ctx.out.kv("version", f"{fields.get('pkgver','?')}-r"
                          f"{fields.get('pkgrel','?')}", 10)

    line = resume_line(arch, args.actions or RESUME_ACTIONS,
                       patches=args.apply_new_patches, pkgrel=args.pkgrel,
                       native2=native2)
    cmd = resume_cmd(arch, line, native2)
    if usable:
        call = " ".join(shlex.quote(a) for a in cmd)
        cmd = in_container(f"{sysroot_mount(arch)} && {call}" if native2
                           else call)
        ctx.out(ctx.out.paint("  resuming IN THE WORKSPACE (container)",
                              "cyan"))
    else:
        ctx.out(ctx.out.paint(f"  resuming ON THE HOST ({why_not})", "cyan"))

    tty = sys.stdout.isatty()
    if args.dry_run or not args.detach:
        ctx.out(ctx.out.paint(watch_hint(tty), "cyan" if tty else "yellow"))
    if args.dry_run:
        print(" ".join(shlex.quote(a) for a in cmd))
        return EX_OK
    if args.detach:
        return _detach(ctx, args, aport, arch)

    # The aport's pkgrel too, and BEFORE the build: the build copy is thrown
    # away by the next full build, so an aport left at the old -rN is how a
    # rebuilt package silently reverts to the version this run replaced.
    if args.pkgrel is not None:
        apkbuild = directory / "APKBUILD"
        apkbuild.write_text(re.sub(r"(?m)^pkgrel=.*$", f"pkgrel={args.pkgrel}",
                                   apkbuild.read_text(errors="replace"),
                                   count=1))
        ctx.out.kv("aport pkgrel", str(args.pkgrel), 10)

    if args.apply_new_patches:
        for patch in sorted(directory.glob("*.patch")):
            if _put_in_tree(ctx, usable, arch, patch, native2):
                raise Bail(f"could not copy {patch.name} into the tree",
                           EX_FAIL, "is the workspace up? `porthole doctor`")
            ctx.out.kv("patch", patch.name, 10)

    env = child_env(os.environ, ctx.cfg)
    env.update(UNBUFFERED)

    packages = _packages_dir(ctx, usable)
    want = expected_apk(packages, arch, fields)
    before = want.exists() and want.stat().st_mtime if want else False

    pmb_log = workdir / "log.txt"
    log_end = pmb_log.stat().st_size if pmb_log.exists() else 0
    with hold(workdir, aport, args.wait):
        rc = build._stream(ctx, cmd, env, args.timeout, f"pkg:{aport}",
                           tracker_cls=progress.PkgTracker, log_prefix="pkg",
                           follow=pmb_log,
                           on_kill=_kill_inside if usable else None)

    landed = want.exists() if want else False
    fresh = landed and (before is False or want.stat().st_mtime != before)
    if rc != 0:
        raise Bail(f"{aport} failed to resume", EX_FAIL,
                   f"the log is in {ctx.root / '.run'}")
    if not landed:
        why = why_nothing_built(log_since(pmb_log, log_end), aport)
        if why:
            raise Bail(why[0], EX_FAIL, why[1])
        raise Bail(f"{aport}: abuild reported success but {want.name} is not "
                   f"there", EX_FAIL, f"looked in {want.parent}")
    if not fresh:
        # Loud, because this is the failure mode a resume actually has: abuild
        # rebuilt nothing and the apk on disk is the one from before.
        raise Bail(f"{aport}: {want.name} was not rewritten", EX_FAIL,
                   "abuild found nothing to redo -- bump with --pkgrel N, or "
                   "touch the sources you edited")
    ctx.out(ctx.out.paint(
        f"  {want.name}  ({want.stat().st_size // 1024} KiB)", "green"))
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


def needs_buildroot_sccache(text: str) -> bool:
    """True when pmbootstrap will run rustc through sccache INSIDE the target
    buildroot for this APKBUILD. Pure.

    pmbootstrap sets RUSTC_WRAPPER=/usr/bin/sccache for every build that is
    not crossdirect while ccache is on (pmb/build/backend.py), but installs
    sccache into the buildroot only during that chroot's one-time build init,
    and only when "rust" or "cargo" is literally a dependency
    (pmb/build/_package.py). Upstream, a strict build zaps the buildroot
    first, so the init runs again. The workspace builds --lax, so it never
    does: the first `!pmb:crossdirect` Rust aport after anything else dies in
    prepare() with "could not execute process `/usr/bin/sccache rustc -vV`".
    """
    deps = set()
    for var in ("depends", "makedepends", "makedepends_build",
                "makedepends_host"):
        for body in _bodies(text, var):
            deps.update(body.split())
    options = " ".join(_bodies(text, "options")).split()
    return bool(deps & {"rust", "cargo", "cargo-auditable"}) and \
        "!pmb:crossdirect" in options


def _arm_buildroot_sccache(arch: str) -> None:
    """Install sccache into the `arch` buildroot, where pmbootstrap will look
    for it (see needs_buildroot_sccache). Best effort, like _arm_ccache: if
    this fails the build fails exactly as it would have, and says why."""
    import subprocess

    import porthole_cmd_sandbox as sandbox

    try:
        subprocess.run(
            ["podman", "exec", sandbox.CONTAINER, "/bin/bash", "-lc",
             f"cd /porthole && pmbootstrap -q chroot -b {shlex.quote(arch)}"
             " --add sccache -- true"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
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
    action = getattr(args, "action", None) or "build"
    argv = [str(porthole), "pkg", action, aport, "--arch", arch,
            "--timeout", str(args.timeout)]
    if getattr(args, "force", False):
        argv.append("--force")
    if getattr(args, "wait", 0):
        argv += ["--wait", str(args.wait)]
    # A resume that dropped these would recompile without the patches, or
    # package the pkgrel it was told to replace -- the same "declared flag that
    # does nothing" the two above were.
    if getattr(args, "apply_new_patches", False):
        argv.append("--apply-new-patches")
    if getattr(args, "pkgrel", None) is not None:
        argv += ["--pkgrel", str(args.pkgrel)]
    if getattr(args, "actions", None):
        argv += ["--actions", args.actions]
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

    import porthole_cmd_build as build

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
    ctx.out.kv("log", str(spawn_log), 10, build.PROGRESS_ONLY)
    ctx.out.kv("build log", str(_log_path(ctx)), 10, build.COMPILER_OUTPUT)
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
    # The block owns the screen while it repaints, so nothing else may write
    # to it: an echoed keystroke -- an idle Enter most of all -- scrolls the
    # terminal out from under the cursor walk, and the block then duplicates
    # itself down the screen instead of replacing itself.
    with progress.quiet_terminal(sys.stdout.isatty()
                                 and not getattr(args, "json", False)):
        return progress.watch(
            rundir, "pkg-status.json", args.interval, out,
            # `--json` is a flag of this verb and `watch` was silently
            # ignoring it: an agent that asked for objects got ANSI
            # repaints down its pipe.
            ndjson=getattr(args, "json", False),
            start_hint="start one with `porthole pkg build <aport>`",
            probe=lambda: running_build(ctx, True),
            log=lambda: _log_path(ctx),
            verdict=lambda name, snap: _verdict(ctx, name, out))


def _log_path(ctx):
    """pmbootstrap's log for whichever buildroot this checkout builds in.

    Resolved lazily, and only when a reattach or a `--detach` banner actually
    needs it: it costs a `_workspace_usable` and the common case never does.
    """
    import porthole_cmd_build as build

    return build.log_path(ctx)


def buildroot_staged_name(workdir):
    """`(pkgname, started)` for whatever that buildroot is building.

    Takes the workdir rather than ctx so the caller can pass the one it has
    already resolved: `_workspace_usable` costs a `podman ps` plus an
    `inspect`, and asking it twice in one `pkg status` doubled the wait on a
    loaded machine for an answer that cannot have changed in between.
    """
    import porthole_buildroot as buildroot

    return buildroot.staged_build_name(workdir)


def _verdict(ctx, aport: str, out) -> int:
    """Did the build we reattached to actually produce its apk?

    A reattached watch has no tracker to have written `done` or `failed`, and
    the alternative to this is believing the log's last line -- which is the
    exact mistake `_build` refuses to make, because pmbootstrap can print
    success and write no apk. Same two helpers `_build` uses, so a watcher and
    the build itself cannot disagree about what "it worked" means.
    """
    import porthole_cmd_build as build
    import porthole_progress as progress

    usable, _ = build._workspace_usable(ctx)
    arch = ctx.cfg.get("PORTHOLE_ARCH") or "aarch64"
    directory = find_aport(_find_pmaports(ctx), aport)
    want = None
    if directory is not None:
        want = expected_apk(_packages_dir(ctx, usable), arch,
                            apkbuild_fields((directory / "APKBUILD").read_text(
                                errors="replace")))
    if want is None:
        out(f"  the build ended. {aport} has no APKBUILD here, so nothing "
            f"could check what it produced\n")
        return EX_FAIL
    if want.exists():
        age = progress.fmt_dur(time.time() - want.stat().st_mtime)
        out(f"  the build ended -- {want.name} landed, "
            f"{want.stat().st_size // 1024} KiB, {age} ago\n")
        return EX_OK
    # The TAIL, not the whole file: log.txt is shared and long-lived, and
    # reading it whole lets a previous run's "is up to date" explain this one
    # -- the bound `log_since` exists for, kept here.
    why = why_nothing_built(progress.log_tail(_log_path(ctx))[0] or "", aport)
    out(f"  the build ended and {want.name} is not there\n")
    if why:
        out(f"  {why[0]}\n")
    return EX_FAIL


def _device_build_times(ctx):
    """{package: builddate} for what the PHONE has installed, or {}.

    apk records it per installed package as `t:` in /lib/apk/db/installed, so
    this is the phone's own answer rather than anything inferred from a name.
    """
    dev = ctx.device()
    if dev.state(max_age=30) != "BOOTED":
        return {}
    rc, out, _ = dev.run_full(
        "awk '/^P:/{p=substr($0,3)} /^t:/{if(p)print p, substr($0,3); p=p}' "
        "/lib/apk/db/installed 2>/dev/null", timeout=90)
    if rc != 0:
        return {}
    times = {}
    for line in out.split("\n"):
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            times.setdefault(parts[0], int(parts[1]))
    return times


def _outdated(ctx) -> int:
    """Which local aports no longer match the .apk built from them -- and which
    apks the DEVICE is older than.

    Two different questions, and only the first one existed. A phone ran a
    five-day-old kernel on 2026-09-09 while this verb said one aport was left
    and ph-pkgcheck said `running kernel #32 == aport r31 + 1  ok`: both
    compared labels, and a rebuild at an unchanged pkgrel moves no label.
    """
    pmaports = _find_pmaports(ctx)
    arch = ctx.cfg.get("PORTHOLE_ARCH") or "aarch64"
    usable, _ = __import__("porthole_cmd_build")._workspace_usable(ctx)
    packages = _packages_dir(ctx, usable)
    stale = outdated(pmaports, packages, arch)

    on_device = _device_build_times(ctx)
    built = {}
    if on_device:
        for apk in packages.glob("*/{}/*.apk".format(arch)):
            stem = apk.name[:-4]
            parts = stem.rsplit("-", 2)
            if len(parts) == 3 and parts[0] in on_device:
                when = apk_builddate(apk)
                if when and when > built.get(parts[0], 0):
                    built[parts[0]] = when
    behind = device_behind({k: v for k, v in on_device.items() if k in built},
                           built)

    def _ago(a, b):
        hours = (b - a) / 3600.0
        return "%.0f h" % hours if hours < 48 else "%.0f days" % (hours / 24)

    def render():
        if not stale:
            ctx.out("every locally built aport matches its apk")
        else:
            ctx.out.heading(f"{len(stale)} aport(s) need rebuilding")
            for name, why in stale:
                ctx.out.kv(name, why, 22)
            ctx.out.blank()
            ctx.out(ctx.out.paint(
                f"  porthole pkg build {stale[0][0]}", "cyan"))
        if not on_device:
            ctx.out.blank()
            ctx.out(ctx.out.paint(
                "  device not read (not BOOTED) -- nothing here says what it "
                "is actually running", "grey"))
            return
        ctx.out.blank()
        if not behind:
            ctx.out("the device runs the newest build of everything we carry")
            return
        ctx.out.heading(f"{len(behind)} package(s) the DEVICE is behind on")
        for name, dev_t, built_t in behind:
            ctx.out.kv(name, "device build is %s older than the apk here"
                       % _ago(dev_t, built_t), 22)
        ctx.out.blank()
        ctx.out(ctx.out.paint(
            f"  porthole pkg install {behind[0][0]} --yes", "cyan"))

    payload = {
        "stale": [{"aport": n, "why": w} for n, w in stale],
        "device_behind": [{"package": n, "device_builddate": d,
                           "built_builddate": b} for n, d, b in behind],
        "device_read": bool(on_device),
    }
    return ctx.emit(payload, render)


def _owned(ctx, args) -> int:
    """What this port carries on top of stock.

    Bare names on stdout, because tools/ph-pkgcheck.sh reads it with $(...)
    and a decorated line would become an argument. The reasoning belongs to
    --json and to the manifest itself.
    """
    import porthole_aports_manifest as man

    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    manifest = man.load(ctx.root, device)
    if not manifest:
        raise Bail(f"{device} has no aports.conf", EX_UNAVAILABLE,
                   f"expected at {man.path_for(ctx.root, device)}")

    complaints = man.problems(manifest)
    tier = getattr(args, "tier", None)
    selected = man.names(manifest, tier)

    def render():
        for name in selected:
            ctx.out(name)
        for problem in complaints:
            ctx.out.warn(problem)

    # problems() reports on the WHOLE manifest, not the tier-filtered
    # subset -- a malformed optional entry is still worth flagging (and
    # failing the exit code for) even when someone only asked for required.
    aports = {n: manifest[n] for n in selected}

    # ctx.emit prints JSON when --json was passed and returns EX_OK either
    # way, so the exit code is ours to decide afterwards.
    ctx.emit({"aports": aports, "problems": complaints}, render)
    return EX_FAIL if complaints else EX_OK


def _git_fetch(cwd, timeout=300):
    """The ONE network call in this module, and only ever opt-in.

    Deliberately not routed through _git_read, whose contract is that it
    never touches the network -- `drift` running offline by default is why
    it is cheap enough that people actually run it. Breaking that promise
    inside the read helper would make every caller a possible network call.
    Returns True when the fetch succeeded; a failure is not fatal, because a
    stale comparison is still worth printing and now says so itself.
    """
    import subprocess

    try:
        done = subprocess.run(["git", "-C", str(cwd), "fetch", "--quiet",
                               "origin"], capture_output=True, text=True,
                              timeout=timeout)
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _git_read(cwd, args, timeout=10):
    """One read-only `git` call against `cwd`: (stdout, returncode).

    Every caller below passes a query against a ref or the index that is
    already local -- `show`, `log`, `symbolic-ref`, `rev-list` -- so this
    never touches the network. `drift` must stay offline: reading a ref is
    free, fetching one is a side effect this command does not get to have.
    """
    try:
        proc = subprocess.run(["git", "-C", str(cwd)] + list(args),
                              capture_output=True, text=True, timeout=timeout)
        return proc.stdout, proc.returncode
    except (OSError, subprocess.SubprocessError):
        return "", 1


def _git_bytes(cwd, args, timeout=10):
    """`_git_read`, but the blob comes back as bytes: (stdout, returncode).

    An aport can hold a binary -- `device-sony-taoshan/logo.rle`,
    `device-xiaomi-latte/MOK.cer` -- and `text=True` does not merely mangle
    those, it raises UnicodeDecodeError partway through reading a tree. Every
    caller that reads FILE CONTENT uses this; the ones reading shas and refs
    keep the text version, because those really are text.
    """
    try:
        proc = subprocess.run(["git", "-C", str(cwd)] + list(args),
                              capture_output=True, timeout=timeout)
        return proc.stdout, proc.returncode
    except (OSError, subprocess.SubprocessError):
        return b"", 1


def upstream_remote_ref(upstream) -> str:
    """`origin/<default-branch>` for the aports_upstream checkout.

    Discovered rather than hardcoded: `git symbolic-ref refs/remotes/
    origin/HEAD` is what `git remote show origin` and a fresh clone both set,
    so a repo whose default branch is not `master` is still read correctly.
    Falls back to `origin/master` both when that symref has never been set
    and when there is no `origin` remote at all -- the second case is what
    `read_upstream_apkbuild`'s own worktree fallback exists for.
    """
    out, rc = _git_read(upstream, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    out = out.strip()
    if rc == 0 and out.startswith("refs/remotes/"):
        return out[len("refs/remotes/"):]
    return "origin/master"


def read_upstream_apkbuild(upstream, up_rel_path: str, ref: str):
    """`(text, source)` for one upstream aport's APKBUILD.

    Reads the REMOTE-TRACKING ref, not the checked-out worktree. The two are
    different things: verified on this host, the worktree sat at a commit
    from 2026-08-20 ("behind 1009" against origin/master) while
    `.git/FETCH_HEAD`'s mtime said "fetched today" -- so a drift check that
    read the worktree and reported that mtime was comparing stale data while
    claiming fresh data. Reading `ref` directly is what makes the date this
    module reports actually describe what was compared.

    Falls back to the worktree file when the ref can't be read (no `origin`
    remote, a shallow clone missing the object, ...), so a checkout without
    one still works -- and `source` says which happened, because a silent
    fallback to the stale thing is the bug being fixed here.
    """
    out, rc = _git_read(upstream, ["show", f"{ref}:{up_rel_path}/APKBUILD"])
    if rc == 0 and out.strip():
        return out, f"git {ref}"
    try:
        text = (pathlib.Path(upstream) / up_rel_path / "APKBUILD").read_text(
            errors="replace")
        return text, f"worktree ({ref} unreadable)"
    except OSError:
        return "", ""


def _drift(ctx, args) -> int:
    """Has upstream moved past a fork we carry?

    Answers the question that actually bites -- would apk prefer upstream's
    build over ours -- rather than "is there a newer version". A fork at
    -r14 reads as safe against 26.1.6-r0 and as at-risk against 26.2.0-r0,
    because apk compares pkgver first.
    """
    import porthole_aports_manifest as man
    import porthole_pmaports as pmap

    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    manifest = man.load(ctx.root, device)
    if not manifest:
        raise Bail(f"{device} has no aports.conf", EX_UNAVAILABLE,
                   f"expected at {man.path_for(ctx.root, device)}")

    pmaports = _find_pmaports(ctx)
    upstream = pmap.find_aports_upstream(pmaports, ctx.cfg)
    ref = upstream_remote_ref(upstream) if upstream else "origin/master"

    # BEFORE the comparison, not after it. Written the wrong way round first,
    # and the result was this verb reporting `last synced 2026-09-20` beside
    # verdicts computed from pre-fetch data -- a fresh date over stale rows,
    # which is a more convincing version of exactly the bug this feature
    # exists to kill. One network round trip, opt-in: fetching on every run
    # would make the check slow enough that people stop running it.
    if getattr(args, "fetch", False) and upstream:
        ctx.out(f"  fetching {upstream} ...")
        if not _git_fetch(upstream):
            ctx.out.warn("the fetch failed -- the comparison below is "
                         "whatever was already on disk, and its age says so")

    rows = {}
    for name, fields in manifest.items():
        up_rel_path = fields.get("upstream", "")
        # Ours outright -- there is no upstream to drift from. A missing
        # `upstream:` field (parse_blocks never sets the key at all) reads
        # identically to the empty string here, same as the `(none -- ...)`
        # sentinel below -- both mean "not a fork", not "unresolved".
        if not up_rel_path or up_rel_path.startswith("("):
            rows[name] = {"verdict": "unknown", "why": "ours, not a fork"}
            continue
        ours_dir = find_aport(pmaports, name)
        theirs_text, source = (
            read_upstream_apkbuild(upstream, up_rel_path, ref)
            if upstream else ("", ""))
        if not ours_dir or not theirs_text:
            # Distinct from "unknown" (ours outright, above): this is a
            # fork the manifest CLAIMS to track, that could not actually be
            # compared -- a wrong upstream path or a missing aport. Saying
            # "unknown" here reads identically to "no upstream exists" and
            # a required fork silently goes unchecked; "unresolved" does not.
            why = (f"{name} not found in pmaports" if not ours_dir else
                   f"upstream path {up_rel_path!r} not found in "
                   f"aports_upstream (looked in {ref} and the worktree)")
            rows[name] = {"verdict": "unresolved", "why": why,
                          "tier": fields.get("tier", "required")}
            continue
        ours = apkbuild_fields((ours_dir / "APKBUILD").read_text(
            errors="replace"))
        theirs = apkbuild_fields(theirs_text)
        call = man.verdict(ours.get("pkgver", ""), ours.get("pkgrel", ""),
                           theirs.get("pkgver", ""), theirs.get("pkgrel", ""))
        # Only OUR patches are at risk. Globbing everything beside our
        # APKBUILD counts Alpine's own patches too -- mesa carries three of
        # those verbatim upstream (23575.patch, llvm22-armhf.patch,
        # riscv64-tls.patch), and nobody loses those when apk picks upstream's
        # build, because upstream's build already has them.
        their_patches, patch_source = (
            upstream_patch_names(upstream, up_rel_path, ref)
            if upstream else (set(), ""))
        patches = sorted({p.name for p in ours_dir.glob("*.patch")}
                         - their_patches)
        rows[name] = {
            "verdict": call,
            "ours": f"{ours.get('pkgver')}-r{ours.get('pkgrel')}",
            "upstream": f"{theirs.get('pkgver')}-r{theirs.get('pkgrel')}",
            "patches": patches,
            "patch_source": patch_source,
            "tier": fields.get("tier", "required"),
            "source": source,
        }

    # "unresolved" joins "loses"/"at-risk" here: not being able to check a
    # required fork is exactly as actionable as it losing, and reporting
    # success in that case is the bug this fix exists for. Optional-tier
    # unresolved stays quiet, same as optional-tier at-risk already does.
    bad = [n for n, r in rows.items()
           if r["verdict"] in ("loses", "at-risk", "unresolved")
           and r.get("tier", "required") == "required"]

    # How stale is the comparison itself? Derived from the SAME ref the
    # comparison above actually read, not from FETCH_HEAD's mtime -- those
    # can disagree (a `git fetch` with no merge bumps FETCH_HEAD's mtime
    # without moving the worktree, and vice versa a stale FETCH_HEAD says
    # nothing about how current `ref` itself is).
    synced = "unknown"
    behind = 0
    if upstream:
        date_out, rc = _git_read(upstream, ["log", "-1", "--format=%cs", ref])
        if rc == 0 and date_out.strip():
            synced = date_out.strip()
        behind_out, rc = _git_read(upstream, ["rev-list", "--count",
                                              f"HEAD..{ref}"])
        if rc == 0 and behind_out.strip().isdigit():
            behind = int(behind_out.strip())

    # Staleness CHANGES the verdict, it does not sit beside it. drift printed
    # "last synced 2026-09-09" on the run that called mesa SAFE eleven days
    # after Alpine had moved past it; the footnote was there and the verdict
    # is what was believed. Only `safe` is downgraded -- see stale_verdict().
    age = man.age_in_days(synced)
    stale = []
    for _name, _row in rows.items():
        was = _row["verdict"]
        now = man.stale_verdict(was, age)
        if now != was:
            _row["verdict"] = now
            _row["stale_age_days"] = age
            stale.append(_name)

    unresolved = [n for n, r in rows.items() if r["verdict"] == "unresolved"]

    def render():
        ctx.out.kv(f"upstream ({ref}) last synced", synced, 28)
        if behind:
            # Visible even though it does not affect the comparison above
            # (that reads `ref` directly, never the worktree) -- unless a
            # row's `source` says it fell back to the worktree, in which
            # case this IS the staleness that row is exposed to.
            ctx.out.warn(f"the aports_upstream WORKTREE is {behind} "
                         f"commit(s) behind {ref} -- fine unless a row below "
                         f"says its APKBUILD came from the worktree")
        ctx.out.blank()
        for name, row in rows.items():
            if row["verdict"] == "unknown":
                continue
            if row["verdict"] == "unresolved":
                # Visible, not skipped: silently dropping this row is the
                # exact failure this feature exists to end (finding 2).
                ctx.out(f"  {name:<28} COULD NOT BE CHECKED -- {row['why']}")
                continue
            ctx.out(f"  {name:<28} {row['ours']:<14} "
                    f"upstream {row['upstream']:<14} {row['verdict'].upper()}")
            if row.get("source", "").startswith("worktree"):
                ctx.out(f"      upstream read from the WORKTREE, not "
                        f"{ref}: {row['source']}")
            if row["verdict"] != "safe" and row["patches"]:
                ctx.out(f"      {len(row['patches'])} patches at risk: "
                        f"{', '.join(row['patches'])}")
        if stale:
            ctx.out.blank()
            ctx.out.warn(
                f"{len(stale)} fork(s) COULD NOT BE JUDGED: the comparison "
                f"is {age} days old ({ref} last moved {synced}), and apk "
                f"compares pkgver before pkgrel, so upstream may have moved "
                f"past them since. This is how mesa 26.2.2-r51 read SAFE "
                f"while the phone ran stock 26.2.3-r0.")
            ctx.out.hint("porthole pkg drift --fetch    refresh, then judge")
        if bad:
            ctx.out.blank()
            ctx.out.warn(f"{len(bad)} carried fork(s) upstream may outrank "
                         f"or could not be checked: {', '.join(bad)}")
            ctx.out.hint("docs/DESIGN-fork-provenance-and-host-sync.md "
                         "section 7 -- rebasing is Plan 3 and not built yet")
        elif unresolved:
            # None of these are required-tier (those are already in `bad`
            # above), so this is informational, not a failure.
            ctx.out(f"{len(unresolved)} optional-tier fork(s) could not be "
                    f"checked: {', '.join(unresolved)}")
        else:
            # Only true when every carried fork was actually compared:
            # reached only when `bad` and `unresolved` are both empty.
            ctx.out("every carried fork still outranks upstream")

    ctx.emit({"aports": rows, "at_risk": bad, "upstream_ref": ref,
              "upstream_synced": synced, "upstream_worktree_behind": behind,
              "upstream_age_days": age, "stale": stale,
              "stale_after_days": man.STALE_AFTER_DAYS},
             render)
    # Stale is a failure too. A verdict nobody can trust must not exit 0, or
    # a CI gate and an agent both read it as "checked, fine".
    return EX_FAIL if (bad or stale) else EX_OK


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

    # The same question `watch` and the status line ask: the tracker can die
    # without the build dying, and reporting the frozen file as `stale` is
    # how this said nothing for two hours about a build sitting at 88%.
    log_path = _log_path(ctx)
    live = progress.reattach_from_log(log_path, snap)
    tracked = live is not None
    # ONLY when this checkout's own snapshot is not a live tracked build.
    #
    # `reattach_from_log` returns None for two opposite reasons -- "the
    # tracker is alive, its own file is better" and "there is nothing to
    # reattach to" -- and this branch treated them the same. So a running
    # `porthole pkg build linux-...` was reported as `pkg:device-google-taimen`
    # at `39m30s`, reconstructed from a buildroot staging directory an
    # unrelated build had left there an hour earlier, with a note saying it
    # had been "started outside `porthole pkg`". Every field wrong, about a
    # build whose own status file was correct and one second old.
    #
    # `build_snapshot` in the status line already had this guard, which is
    # why the two disagreed.
    if live is None and progress.liveness(snap) != "running":
        # The build nobody ever tracked. `reattach` only answers for a
        # snapshot frozen at `running`; a build started outside this verb, or
        # started after the last one wrote `done`, has no snapshot to freeze.
        # Same two file reads the status line makes, so the two cannot
        # disagree about whether something is building.
        name, started = buildroot_staged_name(log_path.parent)
        live = progress.live_build_from_log(log_path, name, started)
    if live is not None:
        snap = dict(live, reattached=True)

    def render():
        head, rows = progress.status_report(
            snap, alive=(lambda pid: True) if live is not None else None)
        ctx.out("  " + head)
        for label, value in rows:
            ctx.out.kv(label, value, 10)
        if live is not None:
            # Two different facts wear one symptom, and `orphaned` exists
            # because the wording is not interchangeable: a tracker that died
            # leaves real-but-frozen numbers, while a `sandbox shell` build
            # was never published at all and never will be. Telling somebody
            # their build "outlived its tracker" when nothing ever tracked it
            # sends them looking for a run that did not exist.
            ctx.out.kv("note",
                       ("reattached: this build outlived the run that was "
                        "tracking it, so these numbers come from the "
                        "workspace log") if tracked else
                       ("nothing published a status file for this build -- it "
                        "was started outside `porthole pkg`, so these numbers "
                        "come from the workspace log and the buildroot"), 10)

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


# --------------------------------------------------------------- rebase --
#
# A fork's delta is three-way by nature: upstream AT FORK TIME is the base,
# our tree is ours, upstream NOW is theirs. `git merge-file` does exactly that
# per file and knows how to mark a conflict, so nothing here re-implements a
# merge. The first draft of this hand-wrote eight comparison branches for the
# cases where two sides happen to be equal; every one of them is a case
# merge-file already gets right, and each was a chance to get it wrong.


# How far back to walk an APKBUILD's history looking for the fork point.
# 400 covers roughly two years for a package bumped as often as mesa; the
# refusal says when it was reached, so 'older than this' is distinguishable
# from 'not there at all'.
BASE_SEARCH_DEPTH = 400


class Entry(NamedTuple):
    """One file in an aport, as git records it: a mode and the raw bytes.

    Content alone is not enough and that was the defect. `git show` on a
    symlink returns the LINK TARGET as blob content, so a reader that keeps
    only content turns `link -> run.sh` into a regular file containing the
    six characters `run.sh`. Same shape for the executable bit and for a
    binary. 382 of pmaports' 1664 aports carry at least one such file.
    """

    mode: str
    blob: bytes

    @property
    def mergeable(self) -> bool:
        """Can `git merge-file` be asked about this, or is it opaque?

        A regular file whose bytes are UTF-8 with no NUL. Everything else --
        symlinks, binaries, anything the terminal would not survive -- is
        carried whole or reported as a conflict, never line-merged. A
        three-way merge of a JPEG produces a JPEG-shaped thing that is not a
        JPEG, and does it without complaining.
        """
        if self.mode not in ("100644", "100755"):
            return False
        if b"\x00" in self.blob:
            return False
        try:
            self.blob.decode()
        except UnicodeDecodeError:
            return False
        return True

    def text(self) -> str:
        return self.blob.decode()


def _tree_at(upstream, ref: str, rel: str) -> dict:
    """`{filename: Entry}` for one aport directory at one git ref.

    One directory deep, deliberately. An aport is a flat directory of an
    APKBUILD and its patches; anything nested would be new, and silently
    flattening it is how a rebase drops a file. Nested paths are reported by
    `_nested_at` rather than merged -- 43 aports have them.
    """
    out, rc = _git_read(upstream, ["ls-tree", "-r", ref, "--", f"{rel}/"])
    if rc != 0:
        return {}
    files = {}
    for line in out.splitlines():
        # `<mode> <type> <sha>\t<path>`
        head, _, path = line.partition("\t")
        parts = head.split()
        if len(parts) != 3 or not path:
            continue
        mode, _kind, sha = parts
        if path.count("/") != rel.count("/") + 1:
            continue
        blob, rc = _git_bytes(upstream, ["cat-file", "blob", sha])
        if rc == 0:
            files[path.rsplit("/", 1)[1]] = Entry(mode, blob)
    return files


def read_aport_dir(path) -> dict:
    """`{filename: Entry}` for an aport on disk, one directory deep.

    Reads the LINK rather than through it. `Path.is_file()` follows a symlink
    and `read_text()` returns the target's content, so our side of a symlink
    looked nothing like git's side of the same symlink -- which produced a
    conflict on a file neither side had touched.
    """
    files = {}
    for f in sorted(pathlib.Path(path).iterdir()):
        if f.is_symlink():
            files[f.name] = Entry("120000", os.readlink(f).encode())
        elif f.is_file():
            mode = "100755" if f.stat().st_mode & 0o111 else "100644"
            files[f.name] = Entry(mode, f.read_bytes())
    return files


def nested_in_dir(path) -> list:
    """Subdirectories of an aport on disk. The mirror of `_nested_at`.

    Warned about for OUR tree too, not just upstream's. The first version
    checked upstream only, so a file nested in our own fork was dropped from
    the plan without a word -- the exact failure the upstream check exists to
    prevent, on the side that would actually lose work.
    """
    return sorted(f.name for f in pathlib.Path(path).iterdir() if f.is_dir())


def _names_at(upstream, ref: str, rel: str):
    """`(paths, ok)` for everything under `rel` at `ref`. Names only.

    Separate from `_tree_at` because two callers want the listing without
    paying `cat-file` per file, and because `ok` distinguishes "the tree is
    empty" from "the ref could not be read" -- a distinction the callers make
    opposite decisions on.
    """
    out, rc = _git_read(upstream, ["ls-tree", "-r", "--name-only", ref,
                                   "--", f"{rel}/"])
    if rc != 0:
        return [], False
    return [p.strip() for p in out.splitlines() if p.strip()], True


def _nested_at(upstream, ref: str, rel: str) -> list:
    """Paths under `rel` that `_tree_at` skipped because they are nested."""
    paths, ok = _names_at(upstream, ref, rel)
    if not ok:
        return []
    return sorted(p for p in paths if p.count("/") != rel.count("/") + 1)


def upstream_patch_names(upstream, rel: str, ref: str):
    """`(names, source)` -- the patches upstream ships for `rel`, AT `ref`.

    READ FROM THE REF, not from the checked-out worktree, and this is a fix
    rather than a preference. `drift` compared VERSIONS against the ref while
    subtracting patches globbed from the worktree, and on this host that
    worktree is 1009 commits behind: measured 2026-09-10, `main/mesa` on disk
    still carries `llvm22-armhf.patch` while `origin/master` has deleted it.

    So `ours - theirs` subtracted a patch upstream no longer ships, and mesa's
    alarm said "3 patches at risk" when the honest answer is four --
    `llvm22-armhf.patch` is now carried only by us, and goes with the rest if
    apk picks upstream's build. #84 claimed this could "overstate risk but not
    mask it"; a STALE worktree masks, and was masking one on the single fork
    the design was written for.

    Falls back to the worktree when the ref cannot be read, and says which
    happened -- a silent fallback to the stale thing is the bug being fixed.
    """
    paths, ok = _names_at(upstream, ref, rel)
    if ok:
        return ({p.rsplit("/", 1)[1] for p in paths
                 if p.endswith(".patch")
                 and p.count("/") == rel.count("/") + 1}, f"git {ref}")
    up_dir = pathlib.Path(upstream) / rel
    if up_dir.is_dir():
        return ({p.name for p in up_dir.glob("*.patch")},
                f"worktree ({ref} unreadable)")
    return set(), ""


def base_ref_for(upstream, rel: str, entry: dict):
    """The aports_upstream commit this fork was taken from, and how we know it.

    `commit:` is authoritative when it is a real sha -- `pkg fork` records one
    at fork time. Every entry that predates that says `unknown`, so the
    fallback walks the APKBUILD's own history for the commit whose
    pkgver-pkgrel is the `forked:` value. Verified on the one real case:
    mesa's `26.1.6-r0` resolves to e744e23b, two upstream bumps back.

    `--follow`, because three of the four forks this port carries have
    histories that cross a `testing/ -> community/` promotion: measured
    2026-09-10, phoc 103 commits without it and 107 with, epiphany 96 and
    108. Without `--follow` a fork taken before its package was promoted is
    simply not findable, and the refusal reads as "wrong version" rather than
    "the file moved".

    Returns `(ref, how)` on success and `(None, why_not)` otherwise -- never a
    guess. Rebasing onto the wrong base silently reclassifies upstream's own
    changes as our delta, which is worse than refusing to start.
    """
    raw = (entry.get("commit", "") or "").strip()
    commit = raw.split()[0] if raw else ""
    if re.fullmatch(r"[0-9a-f]{7,40}", commit):
        _out, rc = _git_read(upstream, ["cat-file", "-e", f"{commit}^{{commit}}"])
        if rc == 0:
            return commit, "recorded in the manifest"

    forked = (entry.get("forked", "") or "").strip()
    if not forked or "-r" not in forked:
        return None, (f"nothing to find a base from: commit: is "
                      f"{commit or 'unset'}, forked: is {forked or 'unset'}")
    want_ver, want_rel = forked.rsplit("-r", 1)
    # `--name-only` alongside `--follow` is what makes the rename usable: it
    # reports the path the file had IN THAT COMMIT. Asking for the file at
    # today's path across a rename fails on every commit before the move, so
    # `--follow` alone finds the commits and then reads nothing from them --
    # which is a refusal that looks exactly like "the version is not there".
    out, rc = _git_read(upstream, ["log", "--format=%H", "--name-only",
                                   "--follow", "-n", str(BASE_SEARCH_DEPTH),
                                   "--", f"{rel}/APKBUILD"], timeout=60)
    if rc != 0 or not out.strip():
        return None, f"cannot read {rel}/APKBUILD's history in aports_upstream"
    seen, sha = [], ""
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[0-9a-f]{40}", line):
            sha = line
        elif sha:
            seen.append((sha, line))
            sha = ""
    for sha, path in seen:
        text, rc = _git_read(upstream, ["show", f"{sha}:{path}"])
        if rc != 0:
            continue
        got = apkbuild_fields(text)
        if got.get("pkgver") == want_ver and got.get("pkgrel") == want_rel:
            moved = "" if path == f"{rel}/APKBUILD" else f" (then at {path})"
            return sha, (f"found by walking {rel}/APKBUILD back to "
                         f"{forked}{moved}")
    # Saying WHICH of the two happened matters: "not in the last 400" is a
    # reason to look further back, "not in all 96" is a reason to doubt the
    # manifest. Reporting both as "not found" sends you to the wrong one.
    capped = len(seen) >= BASE_SEARCH_DEPTH
    return None, (
        f"no commit builds {forked}" + (
            f" in the last {BASE_SEARCH_DEPTH} touching {rel}/APKBUILD -- "
            f"the search hit its depth limit, so it may simply be older"
            if capped else
            f" anywhere in {rel}/APKBUILD's {len(seen)} commits -- "
            f"was it forked from somewhere else?"))


def _merge_one(base: bytes, ours: bytes, theirs: bytes):
    """3-way merge one file. Bytes in, bytes out, `(blob, conflicted)`.

    `git merge-file -p` prints the result and exits with the conflict count,
    so a non-zero exit is data rather than a failure. An exit at or above 128
    IS a failure, and is reported as a conflict keeping our side: a merge that
    could not run must never read as one that ran cleanly.

    BYTES, NOT TEXT, all the way through. With `text=True` Python applies
    universal-newline translation to what git prints, so a patch carrying
    CRLF came back with every line ending rewritten to LF -- a change to
    every line of a file neither side had touched, produced by the reader
    rather than by the merge. git itself was innocent.
    """
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d)
        (p / "ours").write_bytes(ours)
        (p / "base").write_bytes(base)
        (p / "theirs").write_bytes(theirs)
        proc = subprocess.run(
            ["git", "merge-file", "-p", "--diff3",
             "-L", "ours", "-L", "upstream at fork time", "-L", "upstream now",
             str(p / "ours"), str(p / "base"), str(p / "theirs")],
            capture_output=True)
    if proc.returncode >= 128:
        return ours, True
    return proc.stdout, proc.returncode != 0


def _merge_opaque(b, o, t):
    """3-way for a file nothing can line-merge: symlink, binary, mode change.

    Identity only -- whichever side moved wins, and both moving is a conflict
    that keeps ours and says so. This is what `git merge-file` would do if it
    could see modes, and it is the only honest answer for a blob.
    """
    if b is not None and o == b:
        return t, False, "upstream changed it"
    if b is None or t == b or t is None:
        return o, False, "ours"
    if o == t:
        return t, False, "upstream made the same change"
    return o, True, "both changed it, and it cannot be line-merged"


def plan_rebase(base: dict, ours: dict, theirs: dict) -> dict:
    """What the rebased aport should contain, file by file, and why.

    Pure -- three `{filename: Entry}` mappings in, a plan out -- so every case
    is testable with no git repo, no pmaports and no network.

    Only three branches are written here. Everything else, including every
    case where two of the three sides are identical, is handed to
    `git merge-file` (text) or to identity (`_merge_opaque`), both of which
    are already right about all of them.
    """
    plan = {}
    for name in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(name), ours.get(name), theirs.get(name)
        if o is None:
            if b is None:
                plan[name] = {"verdict": "upstream-new", "entry": t,
                              "note": "upstream added it since we forked"}
            else:
                plan[name] = {"verdict": "dropped", "entry": None,
                              "note": "we deleted it" + (
                                  "; upstream has since changed it"
                                  if t is not None and t != b else "")}
        elif t is None:
            plan[name] = {
                "verdict": "ours" if b is None else "upstream-deleted",
                "entry": o,
                "note": ("our addition" if b is None
                         else "upstream deleted it; ours is kept")}
        elif not (o.mergeable and t.mergeable
                  and (b is None or b.mergeable)):
            entry, bad, why = _merge_opaque(b, o, t)
            plan[name] = {
                "verdict": "conflict" if bad else (
                    "unchanged" if entry == o else "merged"),
                "entry": entry,
                "note": f"{why} (not line-mergeable: mode {entry.mode})"}
        else:
            merged, bad = _merge_one(b.blob if b else b"", o.blob, t.blob)
            # The MODE follows the same three-way rule as the content: ours
            # unless we never changed it. A rebase that keeps our patch and
            # drops our +x has not kept our patch.
            mode = o.mode if (b is None or o.mode != b.mode) else t.mode
            plan[name] = {
                "verdict": ("conflict" if bad else
                            "unchanged" if merged == o.blob and mode == o.mode
                            else "merged"),
                "entry": Entry(mode, merged),
                "note": ("both sides changed it" if bad else "")}
    return plan


def write_plan(dest, plan: dict) -> None:
    """Materialise a plan into `dest`, preserving mode and symlink-ness.

    The counterpart to `read_aport_dir`. `tests/test_pkg_rebase_fidelity.py`
    asserts the pair round-trips a real aport byte for byte, because a rebase
    that cannot reproduce an unchanged tree cannot be trusted to report a
    changed one.
    """
    dest = pathlib.Path(dest)
    for name, info in plan.items():
        entry = info["entry"]
        if entry is None:
            continue
        target = dest / name
        if entry.mode == "120000":
            if target.is_symlink() or target.exists():
                target.unlink()
            os.symlink(entry.blob.decode(), target)
            continue
        target.write_bytes(entry.blob)
        os.chmod(target, 0o755 if entry.mode == "100755" else 0o644)


def _needs_checksum(plan: dict, theirs: dict) -> bool:
    """Does the rebased `source=` differ from upstream's?

    If it does, the checksums in the APKBUILD are upstream's and do not cover
    our patches, so the tree does not build until `abuild checksum` runs.
    Reported only when true: a step printed unconditionally is a step people
    learn to skip.
    """
    mine = plan.get("APKBUILD", {}).get("entry")
    up = theirs.get("APKBUILD")
    got = mine.text() if mine and mine.mergeable else ""
    theirs_text = up.text() if up and up.mergeable else ""
    return sorted(_bodies(got, "source")) != sorted(_bodies(theirs_text, "source"))


def _rebase(ctx, args) -> int:
    """Replay our delta onto the current upstream aport, in a scratch worktree.

    Plan 3 of docs/DESIGN-fork-provenance-and-host-sync.md (section 7). It
    answers `pkg drift`'s alarm: drift says mesa is about to lose three
    patches, this is the thing that moves them onto the version that outranks
    us. It stops there. Deciding the result is correct is a person's job, and
    the design says so.

    NEVER TOUCHES THE WORKING BRANCH -- and not as a promise, as a mechanism.
    Everything is written into a `git worktree` on a fresh branch, which is a
    separate directory: pmaports' own checkout, its branch and its index are
    not read for this and cannot be modified by it. The failure this rules out
    is the one the design flags as the reason Plan 3 ships last -- a rebase
    that half-works and leaves the tree it half-worked on in place.
    """
    import porthole_aports_manifest as man
    import porthole_pmaports as pmap

    name = args.target
    if not name:
        raise Bail("rebase what?", EX_USAGE, "porthole pkg rebase mesa")

    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    if not device:
        raise Bail("no device selected -- the manifest says what each fork "
                   "was forked FROM, and there is no manifest without one",
                   EX_USAGE, f"porthole -d <device> pkg rebase {name}")
    manifest = man.load(ctx.root, device)
    if not manifest:
        raise Bail(f"{device} has no aports.conf", EX_UNAVAILABLE,
                   f"expected at {man.path_for(ctx.root, device)}")
    entry = manifest.get(name)
    if entry is None:
        raise Bail(f"{name} is not in {device}'s aports.conf", EX_FAIL,
                   "porthole pkg owned    what this port carries")

    rel = (entry.get("upstream", "") or "").strip()
    if not rel or rel.startswith("("):
        raise Bail(f"{name} is owned outright, not forked from upstream -- "
                   f"there is nothing to rebase onto", EX_FAIL,
                   f"its manifest entry says upstream: {rel or '(unset)'}")

    pmaports = _find_pmaports(ctx)
    ours_dir = find_aport(pmaports, name)
    if not ours_dir:
        raise Bail(f"{name} is in the manifest but not in pmaports", EX_FAIL,
                   f"porthole pkg fork {name} --yes")
    upstream = pmap.find_aports_upstream(pmaports, ctx.cfg)
    if not upstream:
        raise Bail("no Alpine aports checkout on this host",
                   EX_UNAVAILABLE,
                   pmap.missing_aports_upstream_hint(pmaports, ctx.cfg))

    new_ref = upstream_remote_ref(upstream)
    base_ref, how = base_ref_for(upstream, rel, entry)
    if not base_ref:
        raise Bail(f"cannot tell which upstream commit {name} was forked "
                   f"from: {how}", EX_STATE,
                   f"record it by hand in profiles/{device}/aports.conf, or "
                   f"re-fork with `porthole pkg fork {name} --yes`, which "
                   f"writes commit: from now on")

    base = _tree_at(upstream, base_ref, rel)
    theirs = _tree_at(upstream, new_ref, rel)
    if not theirs:
        raise Bail(f"{rel} does not exist at {new_ref} -- upstream may have "
                   f"moved or deleted it", EX_STATE,
                   f"git -C {upstream} log --diff-filter=D -- {rel}")
    ours = read_aport_dir(ours_dir)

    plan = plan_rebase(base, ours, theirs)
    nested = _nested_at(upstream, new_ref, rel)
    ours_nested = nested_in_dir(ours_dir)
    up_apk = theirs.get("APKBUILD")
    up_fields = apkbuild_fields(up_apk.text() if up_apk and up_apk.mergeable
                                else "")
    new_ver = f"{up_fields.get('pkgver', '?')}-r{up_fields.get('pkgrel', '?')}"
    conflicts = [f for f, v in plan.items() if v["verdict"] == "conflict"]

    payload = {
        "aport": name, "upstream": rel, "onto": new_ver,
        "base_ref": base_ref, "base_how": how, "new_ref": new_ref,
        "files": {f: {k: v for k, v in info.items() if k != "text"}
                  for f, info in plan.items()},
        "conflicts": conflicts,
        "needs_checksum": _needs_checksum(plan, theirs),
        "nested_upstream_paths": nested,
        "nested_ours_dirs": ours_nested,
        "worktree": "", "branch": "",
        "existing_worktrees": existing_rebases(pmaports),
    }

    if args.yes:
        branch = f"porthole/rebase-{name}-{new_ver}"
        payload["branch"] = branch
        payload["worktree"] = str(_write_rebase(ctx, pmaports, branch,
                                                ours_dir, plan))

    def render():
        out = ctx.out
        out.kv("aport", f"{name}   ({rel})", 11)
        out.kv("from", f"{entry.get('forked', '?')}   {base_ref[:12]}", 11,
               note=how)
        out.kv("onto", f"{new_ver}   {new_ref}", 11)
        out.blank()
        width = max(len(f) for f in plan) if plan else 0
        for fname, info in plan.items():
            out.kv(fname, info["verdict"], width, note=info["note"])
        out.blank()
        if nested:
            out.warn(f"{rel} has files in subdirectories upstream, which this "
                     f"does not merge: {', '.join(nested)}")
        if ours_nested:
            out.warn(f"OUR {ours_dir.name}/ has subdirectories, which this "
                     f"does not merge and does not copy: "
                     f"{', '.join(ours_nested)} -- carry them across by hand")
        if conflicts:
            out(f"  {len(conflicts)} file(s) conflicted: "
                f"{', '.join(conflicts)}")
            out("  conflict markers are in the file, three-way "
                "(ours / upstream at fork time / upstream now).")
        if payload["needs_checksum"]:
            out("  source= differs from upstream's, so the checksums do not "
                "cover our patches.")
        stale = payload["existing_worktrees"]
        if stale:
            out.blank()
            total = sum(w["bytes"] for w in stale) / (1024 ** 3)
            out.warn(f"{len(stale)} scratch worktree(s) from earlier rebases "
                     f"are still here, {total:.1f} GiB in total")
            for w in stale:
                out.kv(w["branch"], w["path"], 0,
                       note=f"{w['bytes'] / (1024 ** 3):.1f} GiB")
                out.hint(f"git -C {pmaports} worktree remove {w['path']}")

        if not args.yes:
            out.blank()
            out.hint(f"porthole pkg rebase {name} --yes",
                     "write it to a scratch worktree")
            return
        out.blank()
        out.kv("worktree", payload["worktree"], 11, note=f"branch {branch}")
        out.hint(f"git -C {payload['worktree']} diff", "read what changed")
        if payload["needs_checksum"]:
            out.hint(f"cd {payload['worktree']} && abuild checksum",
                     "before it will build")
        out.hint(f"git -C {pmaports} worktree remove {payload['worktree']}",
                 "when you are done with it")

    ctx.emit(payload, render)
    # Conflicts are a real answer, not a crash -- but they are not success
    # either, and a script that rebases a batch must be able to tell.
    return EX_FAIL if conflicts else EX_OK


def existing_rebases(pmaports) -> list:
    """Scratch worktrees a previous `pkg rebase --yes` left behind.

    Reported on EVERY run of the verb, not just after one. Each is a full
    pmaports checkout -- 107 MB measured on 2026-09-10 -- nothing prunes
    them, and `porthole disk` accounts for apk work dirs rather than
    worktrees, so four forgotten rebases is 428 MB that nothing on the host
    mentions. Telling you where you already are beats a report you have to
    know to run.

    Never removes anything. A scratch tree with half a conflict resolved in
    it is work, and this verb does not get to decide that it is not.
    """
    out, rc = _git_read(pmaports, ["worktree", "list", "--porcelain"])
    if rc != 0:
        return []
    found, path = [], ""
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.startswith("branch ") and path:
            branch = line[len("branch "):].strip()
            if branch.startswith("refs/heads/porthole/rebase-"):
                found.append({"path": path,
                              "branch": branch[len("refs/heads/"):],
                              "bytes": _dir_bytes(path)})
            path = ""
    return found


def _dir_bytes(path) -> int:
    """Bytes under `path`, or 0 if it cannot be walked. Never raises: this is
    decoration on a report, and a report that dies counting is worse than one
    that says nothing."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                with contextlib.suppress(OSError):
                    total += os.lstat(os.path.join(root, f)).st_size
    except OSError:
        return 0
    return total


def _write_rebase(ctx, pmaports, branch: str, ours_dir, plan: dict):
    """Materialise the plan in a fresh git worktree on `branch`.

    The worktree is created BEFORE anything is written and removed again if
    writing fails, so the two outcomes are "a complete scratch tree" and
    "nothing" -- never a half-written one, which is exactly the half-working
    rebase the design says to avoid.
    """
    out, rc = _git_read(pmaports, ["rev-parse", "--verify", branch])
    if rc == 0:
        raise Bail(f"branch {branch} already exists in pmaports", EX_STATE,
                   f"a previous rebase left it. Read it, then "
                   f"`git -C {pmaports} branch -D {branch}`")

    # Deliberately NOT inside pmaports. A worktree there is an untracked
    # directory in pmaports' own working tree, which `porthole sync` then
    # correctly refuses to sync -- a rebase that blocks the next sync is a
    # rebase nobody runs twice. PORTHOLE_RUNDIR is gitignored run state and
    # already the home for exactly this kind of thing.
    rundir = ctx.cfg.get("PORTHOLE_RUNDIR") or str(pathlib.Path(ctx.root) / ".run")
    dest = pathlib.Path(rundir) / "rebase" / branch.rsplit("/", 1)[1]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise Bail(f"{dest} is already there from a previous rebase", EX_STATE,
                   f"git -C {pmaports} worktree remove {dest}")
    proc = subprocess.run(
        ["git", "-C", str(pmaports), "worktree", "add", "-q", "-b", branch,
         str(dest)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise Bail(f"could not create a scratch worktree in pmaports: "
                   f"{proc.stderr.strip() or proc.stdout.strip()}", EX_FAIL)

    try:
        target = dest / ours_dir.relative_to(pmaports)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        write_plan(target, plan)
    except (OSError, ValueError):
        subprocess.run(["git", "-C", str(pmaports), "worktree", "remove",
                        "--force", str(dest)], capture_output=True)
        subprocess.run(["git", "-C", str(pmaports), "branch", "-D", branch],
                       capture_output=True)
        raise
    return dest


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
    upstream = pmap.find_aports_upstream(pmaports, ctx.cfg)
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
            o.hint(f"porthole pkg fork {first['name']} --yes",
                   "copy it into pmaports, where a build can see it")
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

    upstream = pmap.find_aports_upstream(pmaports, ctx.cfg)
    if not upstream:
        # 69: there is nothing here that could fork, which is not a statement
        # about the package.
        raise Bail("no Alpine aports checkout on this host",
                   EX_UNAVAILABLE,
                   pmap.missing_aports_upstream_hint(pmaports, ctx.cfg))

    hits = sorted(upstream.glob(f"*/{name}"))
    if not hits:
        message, hint = missing_aport_hint(pmaports, upstream, name)
        raise Bail(message, EX_FAIL, hint)

    ctx.out.kv("package", f"{name}   ({hits[0].parent.name}/)", 9)
    ctx.out.kv("from", str(upstream), 9)
    ctx.out.kv("into", f"{pmaports}/temp/", 9)
    if not args.yes:
        ctx.out.blank()
        ctx.out.hint(f"porthole pkg fork {name} --yes", "to actually do it")
        return EX_OK

    # Checked before doing any real work, not just before recording it: with
    # no device resolved, `man.append` below would write into
    # `profiles/aports.conf` -- a path with no device segment, that
    # `man.load` never reads back. Failing after aportgen already ran would
    # leave a real fork nobody's manifest knows about.
    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    if not device:
        raise Bail("no device selected -- there is nowhere to record this "
                   "fork's provenance", EX_USAGE,
                   f"porthole -d <device> pkg fork {name} --yes")

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

    # Record where it came from, now, while we still know. A fork whose
    # origin is not written down cannot be rebased later: temp/mesa says
    # nothing about which tree or version it was taken from, and that is
    # why every backfilled entry says `commit: unknown`.
    import porthole_aports_manifest as man

    path = man.append(ctx.root, device,
                      fork_provenance(upstream, hits[0], name))
    ctx.out.kv("recorded", str(path.relative_to(ctx.root)), 9)
    ctx.out.hint(f"edit {path.name}: say what breaks without {name}")

    ctx.out.hint(f"porthole pkg build {name} --detach")
    ctx.out.hint("porthole pkg watch")
    return EX_OK


def fork_provenance(upstream, aport_dir, name: str) -> str:
    """The manifest entry for a fork just taken from `aport_dir`.

    Extracted from `_fork` so it can be RUN. #84 shipped this inline and said
    so: "verified by matching the call against the unit-tested entry_text()
    helper -- this is the one integration in the branch nobody has run".
    Argument order and field names matching by inspection is exactly the
    check that passes while the wiring is wrong.

    The WORKTREE's HEAD is the right commit here, unlike everywhere else in
    this module. `pkg drift` and `pkg rebase` read the remote-tracking ref
    because they ask what upstream has NOW; this asks what we just copied,
    and `pmbootstrap aportgen --fork-alpine` copies the files on disk. A fork
    taken from a checkout 1009 commits behind came from that commit, and
    recording origin/master would be a confident lie that `pkg rebase` would
    later rebase against.
    """
    up_fields = apkbuild_fields(
        (pathlib.Path(aport_dir) / "APKBUILD").read_text(errors="replace"))
    commit, rc = _git_read(upstream, ["rev-parse", "--short", "HEAD"])
    return man_module().entry_text(
        name, f"{pathlib.Path(aport_dir).parent.name}/{name}",
        up_fields.get("pkgver", "?"), up_fields.get("pkgrel", "?"),
        commit.strip() if rc == 0 and commit.strip() else "unknown")


def man_module():
    import porthole_aports_manifest as man

    return man


def build_module():
    import porthole_cmd_build as build

    return build


# ------------------------------------------------ putting one on the phone --

def apks_for_device(installed, apks, version, requested=""):
    """[(name, path)] -- the built apks this device should be given.

    Pure. "Already has" is the rule: an aport's subpackages include things
    this phone does not use (mesa builds vulkan-intel, -broadcom, -panfrost),
    and installing them because they exist would add packages nobody asked
    for. The device's own list decides.

    `requested` is the exception, and the only one: a package named on the
    command line was asked for by definition. Without it a subpackage
    deliberately kept off the device -- which is what a subpackage is FOR --
    could never be installed by the tooling at all (#107).

    `version` is the aport's current `pkgver-pkgrel`, so a stale apk from an
    older build is never picked up by accident.
    """
    want = set(installed) | ({requested} if requested else set())
    out = []
    for path in sorted(apks):
        name = path.name
        if not name.endswith(".apk"):
            continue
        stem = name[:-4]
        suffix = "-" + version
        if not stem.endswith(suffix):
            continue
        pkg = stem[:-len(suffix)]
        if pkg in want:
            out.append((pkg, path))
    return out


def install_verdict(before, after, transaction):
    """"" if the transaction was safe, else why it was not. Pure.

    The count is the signal, and apk prints it itself (`OK: <size> in N
    packages`). A DROP means packages were removed under you -- which is how a
    sideloaded device apk once took the whole radio stack with it (rmtfs,
    tqftpserv, pd-mapper), leaving a modem in a 40-second fatal-error loop and
    wifi dead with it, diagnosed for a day as a kernel fault.
    brain/traps/a-sideloaded-device-apk-can-eat-the-radio-stack.md

    A RISE is fine and normal: a new dependency. Installing mesa pulled in
    xcb-util-keysyms on 2026-09-09 and that was correct.
    """
    if before is None or after is None:
        return "could not count packages before and after -- verify by hand"
    if after < before:
        return ("package count DROPPED {} -> {}: apk removed {} package(s) to "
                "satisfy this install. Do not trust the rootfs; read the "
                "transaction above and see "
                "brain/traps/a-sideloaded-device-apk-can-eat-the-radio-stack.md"
                .format(before, after, before - after))
    return ""


def apk_builddate(path):
    """The `builddate` inside an .apk, or None. Pure w.r.t. the file.

    Read out of .PKGINFO rather than taken from the file's mtime, because
    mtime is what `outdated` had to stop trusting: a copy, a restore or a
    checkout restamps it without changing a byte.
    """
    import tarfile
    try:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                # NOT lstrip("./"): that strips any of those characters, so
                # ".PKGINFO" becomes "PKGINFO" and never matches.
                if member.name in (".PKGINFO", "./.PKGINFO"):
                    data = tar.extractfile(member).read().decode("utf-8", "replace")
                    for line in data.splitlines():
                        if line.startswith("builddate"):
                            return int(line.split("=", 1)[1].strip())
                    return None
    except (OSError, tarfile.TarError, ValueError):
        return None
    return None


def device_behind(installed, built, slack=60):
    """[(name, device_time, built_time)] for packages the phone runs older than
    what has been built here. Pure.

    This is the question neither check could answer, and it is why a phone ran
    a five-day-old kernel while `pkg outdated` said one aport was left and
    `ph-pkgcheck` said `running kernel #32 == aport r31 + 1  ok`. Both compared
    LABELS -- a pkgrel, a build number -- and a rebuild at an unchanged pkgrel
    is invisible to both. Build time is the thing that actually moved.

    `slack` because the two clocks are not the same clock and a package
    installed seconds after it was built must not read as behind.
    """
    out = []
    for name, dev_t in sorted(installed.items()):
        built_t = built.get(name)
        if dev_t is None or built_t is None:
            continue
        if built_t - dev_t > slack:
            out.append((name, dev_t, built_t))
    return out


def _install(ctx, args) -> int:
    """Put an aport's already-built apks onto the running device.

    The gap this fills: every build verb stops at the apk. Getting it onto the
    phone was a hand-rolled `scp` + `apk add` -- done four times in one session
    on 2026-09-09 -- which is exactly what the no-hand-rolling rule exists to
    stop, except there was nothing to reach for.

    It is deliberately NOT a reinstall. `build kernel` and `build image` mkfs
    the rootfs; this writes nothing but the packages named, so the update loop
    for "carry a patch until it lands upstream" costs a build and a few
    seconds rather than a reflash and a restored home directory.
    """
    import porthole

    aport = (args.target or "").strip()
    if not aport:
        raise Bail("which aport?", EX_USAGE,
                   "porthole pkg install <aport>    # e.g. mesa")

    import porthole_cmd_build as build
    import porthole_pmaports as pmap
    usable, _why_not = build._workspace_usable(ctx)
    pmaports = _find_pmaports(ctx)
    if not pmaports:
        raise Bail("no pmaports checkout found", EX_UNAVAILABLE,
                   "`porthole doctor` names how to get one")
    requested = aport
    directory = find_aport(pmaports, aport)
    if directory is None:
        # The name apk installs under is a subpackage name as often as not,
        # and that is the name the caller knows.
        directory, parent = find_subpackage(pmaports, aport)
        if directory is None:
            message, hint = missing_aport_hint(
                pmaports, pmap.find_aports_upstream(pmaports, ctx.cfg), aport)
            raise Bail(message, EX_USAGE, hint)
        ctx.out("{} is a subpackage of {}".format(requested, parent))
        aport = parent
    fields = apkbuild_fields((directory / "APKBUILD").read_text(errors="replace"))
    version = "{}-r{}".format(fields.get("pkgver"), fields.get("pkgrel"))

    arch = args.arch or ctx.cfg.get("PORTHOLE_ARCH") or "aarch64"
    packages = _packages_dir(ctx, usable)
    apks = list(packages.glob("*/{}/*.apk".format(arch))) or \
        list(packages.glob("*.apk"))

    dev = ctx.device()
    if dev.state(max_age=30) != "BOOTED":
        raise Bail("the device is not BOOTED", EX_STATE,
                   "this installs onto the running system; bring it up first")

    rc, out, _err = dev.run_full("apk info", timeout=60)
    installed = out.split() if rc == 0 else []
    if not installed:
        raise Bail("could not list the device's packages", EX_FAIL,
                   "check `porthole doctor`")

    chosen = apks_for_device(installed, apks, version, requested)
    if not chosen:
        ctx.out("nothing to install: no built {}-{} apk matches a package this "
                "device has".format(requested, version))
        ctx.out(ctx.out.paint("  porthole pkg build {}    # build it first"
                              .format(aport), "cyan"))
        return EX_OK

    def render_plan():
        ctx.out.heading("install {} {}".format(aport, version))
        for name, path in chosen:
            ctx.out("  {:<34} {:.1f} MiB".format(
                name, path.stat().st_size / 1048576))

    if not getattr(args, "yes", False):
        render_plan()
        ctx.out.blank()
        ctx.out(ctx.out.paint(
            "  porthole pkg install {} --yes    # install the {} package(s) "
            "above".format(requested, len(chosen)), "cyan"))
        return EX_OK

    render_plan()
    rc, out, _ = dev.run_full("apk info | wc -l", timeout=45)
    before = int(out.strip()) if rc == 0 and out.strip().isdigit() else None

    argv = ["scp", *porthole.ssh_opts(ctx.cfg)]
    argv += [str(path) for _n, path in chosen]
    argv.append("{}:/tmp/".format(dev.phone))
    done = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise Bail("could not copy the apks to the device: {}"
                   .format((done.stderr or "").strip()[:200]), EX_FAIL,
                   "is the device still reachable?")

    remote = " ".join("/tmp/{}".format(path.name) for _n, path in chosen)
    # ONE transaction, and the whole of it is printed. Never `tail -1` an apk
    # transaction: the line that matters can be anywhere in it.
    rc, out, _ = dev.run_full(
        "sudo -n apk add --allow-untrusted {} 2>&1".format(remote), timeout=900)
    ctx.out.blank()
    for line in (out or "").strip().splitlines():
        ctx.out("  " + line)

    rc2, out2, _ = dev.run_full("apk info | wc -l", timeout=45)
    after = int(out2.strip()) if rc2 == 0 and out2.strip().isdigit() else None
    problem = install_verdict(before, after, out)
    ctx.out.blank()
    if problem:
        ctx.out.warn(problem)
        return EX_FAIL
    ctx.out("packages {} -> {}, nothing removed".format(before, after))
    return EX_OK if rc == 0 else EX_FAIL


def cmd_pkg(args, ctx) -> int:
    action = args.action or "status"
    if action == "status":
        return _status(ctx)
    if action == "watch":
        return _watch(ctx, args)
    if action == "outdated":
        return _outdated(ctx)
    if action == "owned":
        return _owned(ctx, args)
    if action == "drift":
        return _drift(ctx, args)
    if action == "rebase":
        return _rebase(ctx, args)
    if action == "stop":
        return _stop(ctx)
    if action == "search":
        return _search(ctx, args)
    if action == "fork":
        return _fork(ctx, args)
    if action == "resume":
        return _resume(ctx, args)
    if action == "install":
        return _install(ctx, args)
    return _build(ctx, args)


SPEC = {
    "verb": "pkg",
    "order": 21,
    "group": "build",
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
        "RESUME KEEPS THE TREE. `pmbootstrap build` runs abuild's whole\n"
        "sequence and deletes /home/pmos/build first, so \"recompile three\n"
        "files and repackage\" costs a full build -- 5.5 hours, for webkit.\n"
        "`resume` runs abuild's build/rootpkg actions against the tree that\n"
        "is already there, under the same lock, tracker and bar.\n\n"
        "TWO TREES, AND ONLY ONE OF THEM BUILDS. pmbootstrap keeps pmaports\n"
        "and Alpine's aports side by side, and `pmbootstrap build` reads\n"
        "pmaports only -- so Alpine's twelve thousand packages are present,\n"
        "useful, and unbuildable until `fork` copies one across. `search`\n"
        "looks in both and says which tree a name is in, which is the actual\n"
        "answer to \"why does my build say the package does not exist\".\n\n"
        "See docs/HANDOFF-package-builds.md."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["build", "install", "resume", "search",
                                  "fork", "status", "watch", "outdated",
                                  "stop", "owned", "drift", "rebase"],
                      "help": "build | install | resume | search | fork | "
                              "status | watch | outdated | stop | owned | "
                              "drift | rebase"}),
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
        (["--pkgrel"], {"type": int, "metavar": "N",
                        "help": "resume: set pkgrel in the aport AND in the "
                                "build tree's copy"}),
        (["--apply-new-patches"], {"action": "store_true",
                                   "help": "resume: copy the aport's *.patch "
                                           "into the tree and apply them to "
                                           "src/ (abuild's prepare is skipped)"}),
        (["--actions"], {"metavar": "LIST",
                         "help": f"resume: abuild functions to run "
                                 f"(default: {RESUME_ACTIONS})"}),
        (["--interval"], {"type": float, "default": 1.0,
                          "help": "watch: seconds between reads (default 1)"}),
        (["--wait"], {"type": float, "default": 0.0, "metavar": "SECONDS",
                      "help": "build: queue this long for the buildroot "
                              "instead of refusing"}),
        (["--yes"], {"action": "store_true",
                     "help": "fork: actually write into pmaports. "
                             "install: actually write to the device -- "
                             "without it, install only lists what it would "
                             "put there. rebase: actually create the "
                             "scratch worktree -- without it, rebase only "
                             "reports what the merge would do"}),
        (["--tier"], {"choices": ("required", "optional"),
                     "help": "owned: only this tier (default: all)"}),
        (["--fetch"], {"action": "store_true",
                       "help": "drift: git fetch the upstream aports clone "
                               "first, so the comparison is current"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "escapes_scope": True,
    "run": cmd_pkg,
    "examples": [
        "porthole pkg search calculator",
        "porthole pkg fork gnome-calculator --yes",
        "porthole pkg build phoc",
        "porthole pkg build webkit2gtk-6.0 --detach",
        "porthole pkg resume webkit2gtk-6.0",
        "porthole pkg resume webkit2gtk-6.0 --apply-new-patches --pkgrel 53",
        "porthole pkg watch",
        "porthole pkg outdated",
        "porthole pkg status --json",
        "porthole pkg stop",
        "porthole pkg owned --tier required",
        "porthole pkg drift --json",
        "porthole pkg rebase mesa",
        "porthole pkg rebase mesa --yes",
    ],
}
