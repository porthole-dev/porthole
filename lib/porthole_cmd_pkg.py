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
import json
import os
import pathlib
import re
import shlex
import shutil
import sys
import time

from porthole_cli import Bail, EX_FAIL, EX_LOCK, EX_OK

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
    call = " ".join(argv)
    # PYTHONUNBUFFERED is not a nicety, it is what makes this verb work at
    # all. pmbootstrap is Python; writing to a pipe rather than a tty it
    # switches to block buffering and holds its output until it exits.
    # Measured: five minutes into a gst-plugins-good build, with cc and lto1
    # visibly running inside the container, the log file was ZERO BYTES and
    # the bar had never moved. Every line arrives at the end, which is exactly
    # the black box this was built to replace.
    return ["podman", "exec", "-e", "PYTHONUNBUFFERED=1", sandbox.CONTAINER,
            "/bin/bash", "-lc", f"cd /porthole && {call}"]


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
    """pmbootstrap's own work dir, on the HOST filesystem either way."""
    import porthole_cmd_sandbox as sandbox

    if in_container:
        return sandbox._sandbox_pmb(ctx.cfg)
    host = ctx.cfg.get("PORTHOLE_PMB_DIR") or "~/.local/var/pmbootstrap"
    return pathlib.Path(host).expanduser()


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
        raise Bail(f"no aport named {aport} under {pmaports}", EX_FAIL,
                   "`porthole aports` lists what is there")

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
        raise Bail(f"no workspace and no pmbootstrap on PATH ({why_not})",
                   EX_FAIL, "run `porthole sandbox up` first")

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
    # makes the ninja `[N/M]` fraction reachable at all.
    with hold(workdir, aport, args.wait):
        rc = build._stream(ctx, cmd, env, args.timeout, f"pkg:{aport}",
                           tracker_cls=progress.PkgTracker, log_prefix="pkg",
                           follow=workdir / "log.txt",
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
    argv = [str(ctx.root / "bin" / "porthole"), "pkg", "build", aport,
            "--arch", arch, "--timeout", str(args.timeout)]
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


def _watch(ctx, args) -> int:
    """Follow the status file until the build stops.

    This exists so that watching a build costs NOTHING. A human leaves this
    open in a second terminal and gets the same bar the build prints; an agent
    never has to poll, because it can start the build as a background job and
    be told when it exits. The failure mode this replaces is an agent burning
    a request every thirty seconds to re-read a number that changed by 1%.

    Polling a file, not sleeping through the build: the sleep here is between
    reads of a real signal, which is what brain/laws/poll-never-sleep.md asks
    for rather than what it forbids.
    """
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    path = rundir / "pkg-status.json"
    tty = os.isatty(1)

    # A just-detached build has not published yet. Wait a little rather than
    # reporting "no build" for the build we were asked to watch.
    appear = time.time() + 30
    while not path.exists() and time.time() < appear:
        time.sleep(0.25)
    if not path.exists():
        raise Bail("no package build has published a status here", EX_FAIL,
                   "start one with `porthole pkg build <aport>`")

    last_note = 0.0
    snap = {}
    started_watching = time.time()
    while True:
        try:
            snap = json.loads(path.read_text())
        except (OSError, ValueError):
            # A read that lands mid-rename. The writer is atomic, so the next
            # one succeeds; treating this as an error would end the watch on a
            # race that resolves itself in 500ms.
            time.sleep(args.interval)
            continue
        live = progress.liveness(snap)
        # A run that had already finished before this watch began is somebody
        # else's build. Keep waiting for ours rather than reporting theirs --
        # belt and braces behind publish_pending, for a `watch` started by
        # hand rather than straight after `--detach`.
        stopped = progress.finished_at(snap)
        if (live != "running" and stopped is not None
                and stopped < started_watching and time.time() < appear):
            time.sleep(args.interval)
            continue
        if tty:
            print("\r\033[2K  " + progress.line_of(snap), end="", flush=True)
        elif time.time() - last_note > max(args.interval, 15):
            last_note = time.time()
            print("  " + progress.line_of(snap), flush=True)
        if live != "running":
            break
        time.sleep(args.interval)

    if tty:
        print("\r\033[2K", end="")
    head, rows = progress.status_report(snap)
    ctx.out("  " + head)
    for label, value in rows:
        ctx.out.kv(label, value, 10)
    return EX_OK if progress.liveness(snap) == "done" else EX_FAIL


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


def stop_plan(snap) -> tuple:
    """`("kill", pid)` or `("none", 0)`. Pure, so the decision is testable.

    Only a run that is still marked running is worth killing; signalling a pid
    from a finished snapshot risks hitting whatever inherited that number.
    """
    if not snap or snap.get("state") != "running":
        return ("none", 0)
    pid = snap.get("pid")
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
    return _build(ctx, args)


SPEC = {
    "verb": "pkg",
    "order": 21,
    "help": "build a userspace aport, with a real progress bar",
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
        "See docs/HANDOFF-package-builds.md."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": ["build", "status", "watch", "outdated", "stop"],
                      "help": "build | status | watch | outdated | stop"}),
        (["target"], {"nargs": "?", "metavar": "APORT",
                      "help": "build: the aport to build"}),
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
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_pkg,
    "examples": [
        "porthole pkg build phoc",
        "porthole pkg build webkit2gtk-6.0 --detach",
        "porthole pkg watch",
        "porthole pkg outdated",
        "porthole pkg status --json",
        "porthole pkg stop",
    ],
}
