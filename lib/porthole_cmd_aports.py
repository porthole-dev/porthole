# SPDX-License-Identifier: MIT
"""`porthole aports` -- work on pmaports without losing track of what you changed.

pmaports is a shared checkout that pmbootstrap also writes to, sitting on a
branch that a channel switch will move under you. Two people working the same
device need to know, at a glance: what branch am I on, what have I actually
changed, and is any of it ready to send upstream.

Every action here is either read-only or an ordinary git operation you could
have typed. Nothing rewrites history and nothing pushes.
"""
from __future__ import annotations

import os
import pathlib
import re
import shlex
import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_UNAVAILABLE, EX_USAGE
import porthole_pmaports as pmap
import porthole_series


def git(pmaports, *args, check=False, timeout=60):
    """Run git in the pmaports checkout. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(["git", "-C", str(pmaports), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise Bail(f"git {' '.join(args)} failed: {exc}", EX_FAIL) from None
        return 1, "", str(exc)
    if check and proc.returncode != 0:
        raise Bail(f"git {' '.join(args)}: {proc.stderr.strip()}", EX_FAIL)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _pmaports(ctx) -> pathlib.Path:
    path = pmap.find_pmaports(ctx.cfg)
    if not path:
        raise Bail("no pmaports checkout found", EX_FAIL,
                   "porthole init    adopts a checkout or clones one, and "
                   "writes the key that names it")
    if not (path / ".git").exists():
        raise Bail(f"{path} is not a git checkout", EX_FAIL)
    return path


def _device_paths(ctx, pmaports) -> list[str]:
    """The pmaports paths that belong to THIS device.

    Named from the profile, not globbed. `linux-postmarketos-*` matches all 40
    kernel packages in the tree, which turns "your device's packages" into a
    directory listing and makes the answer worthless.
    """
    cfg = ctx.cfg
    codename = cfg.get("PORTHOLE_CODENAME") or cfg.get("PORTHOLE_DEVICE", "")
    if not codename:
        return []

    names = {f"device-{codename}"}
    for key in ("PORTHOLE_DEVICE_PKG", "PORTHOLE_FW_PKG", "PORTHOLE_KERNEL_PKG"):
        if cfg.get(key):
            names.add(cfg[key])
    # A kernel aport is often versioned (`...-msm8998-6.18`) while the tree also
    # carries the unversioned one; both are ours.
    kernel = cfg.get("PORTHOLE_KERNEL_PKG", "")
    if kernel:
        names.add(re.sub(r"-\d+\.\d+$", "", kernel))
    if cfg.get("PORTHOLE_SOC"):
        names.add(f"soc-qcom-{cfg['PORTHOLE_SOC']}")   # vendor prefix varies;
        names.add(f"soc-{cfg['PORTHOLE_SOC']}")        # try both shapes

    out = []
    for name in sorted(names):
        for path in pmaports.glob(f"device/*/{name}"):
            if path.is_dir():
                out.append(str(path.relative_to(pmaports)))
    return sorted(set(out))


def local_forks(ctx, pmaports, exclude=()) -> list[str]:
    """Packages THIS branch has changed, which is a different question.

    `_device_paths` answers "named after my phone", and that was the only
    answer porthole had. It is the wrong one for a fork: temp/gst-plugins-good
    is this port's work and is named after gstreamer, so it -- along with
    temp/phoc and temp/webkit2gtk-6.0 -- was invisible in every listing while
    being the thing under active development.

    What makes a package yours is a commit, not a name. So: every package
    directory touched since the channel's branch, plus anything dirty in the
    working tree right now.
    """
    base = _channel_branch(ctx, pmaports) or "origin/master"
    touched = set()
    rc, _, _ = git(pmaports, "rev-parse", "--verify", "--quiet", base)
    if rc == 0:
        rc, out, _ = git(pmaports, "log", "--format=", "--name-only",
                         f"{base}..HEAD", timeout=120)
        if rc == 0:
            touched.update(out.splitlines())
    rc, out, _ = git(pmaports, "status", "--porcelain")
    if rc == 0:
        touched.update(line[3:].strip() for line in out.splitlines()
                       if line.strip())

    # The package directory is the parent of the changed file -- an APKBUILD,
    # a patch, a deviceinfo. Kept only when that parent still holds an
    # APKBUILD, which drops both tree-root files (README, channels.cfg) and
    # packages deleted since.
    found = set()
    for path in touched:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent and (pmaports / parent / "APKBUILD").is_file():
            found.add(parent)
    return sorted(found - set(exclude))


# ------------------------------------------------------------------ status --

def cmd_status(args, ctx, pmaports) -> int:
    _, branch, _ = git(pmaports, "rev-parse", "--abbrev-ref", "HEAD")
    _, porcelain, _ = git(pmaports, "status", "--porcelain")
    changes = [l for l in porcelain.splitlines() if l.strip()]

    upstream = ""
    ahead = behind = 0
    rc, tracking, _ = git(pmaports, "rev-parse", "--abbrev-ref",
                          "--symbolic-full-name", "@{upstream}")
    if rc == 0 and tracking:
        upstream = tracking
        _, counts, _ = git(pmaports, "rev-list", "--left-right", "--count",
                           f"{tracking}...HEAD")
        parts = counts.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])

    mine = _device_paths(ctx, pmaports)
    mine_changed = [c for c in changes
                    if any(c[3:].startswith(p) for p in mine)]
    forks = local_forks(ctx, pmaports, exclude=mine)

    payload = {
        "pmaports": str(pmaports), "branch": branch, "upstream": upstream,
        "ahead": ahead, "behind": behind,
        "changes": changes, "device_paths": mine,
        "device_changes": mine_changed, "forks": forks,
        "clean": not changes,
    }

    def render():
        o = ctx.out
        o.heading(f"pmaports — {branch}")
        o.kv("path", str(pmaports), 12)
        if upstream:
            drift = []
            if ahead:
                drift.append(o.paint(f"{ahead} ahead", "green"))
            if behind:
                drift.append(o.paint(f"{behind} behind", "yellow"))
            o.kv("tracking", f"{upstream}" + (f"  ({', '.join(drift)})" if drift else ""), 12)
        else:
            o.kv("tracking", o.paint("nothing — a local-only branch", "yellow"), 12)
        o.blank()

        if not changes:
            o(o.paint("  working tree clean", "green"))
        else:
            o.heading(f"uncommitted ({len(changes)})")
            for line in changes[:30]:
                mark = line[:2]
                colour = "red" if "?" in mark else "yellow"
                own = any(line[3:].startswith(p) for p in mine)
                tag = o.paint("  <- your device", "cyan") if own else ""
                o(f"  {o.paint(mark, colour)} {line[3:]}{tag}")
            if len(changes) > 30:
                o(f"  ... and {len(changes) - 30} more")
        o.blank()
        if mine:
            o.heading("your device's packages")
            for path in mine:
                o(f"  {path}")
            o.blank()
        if forks:
            o.heading("packages you have forked")
            for path in forks:
                o(f"  {path}")
            o.blank()
        o.hint("porthole aports diff            what changed")
        o.hint("porthole aports start <topic>   a branch for a new change")
        o.hint("porthole aports patch           a series ready to send")
        o.hint("porthole pkg search <text>      what else is buildable")

    return ctx.emit(payload, render)


# ------------------------------------------------------------------- start --

BRANCH_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")


def cmd_start(args, ctx, pmaports) -> int:
    topic = args.name
    if not topic:
        raise Bail("name the topic", EX_USAGE,
                   "porthole aports start <topic>")
    if not BRANCH_RE.match(topic):
        raise Bail(f"branch names are lowercase alphanumerics, dots, dashes and "
                   f"slashes: {topic!r}", EX_USAGE)

    _, porcelain, _ = git(pmaports, "status", "--porcelain")
    if porcelain.strip() and not args.allow_dirty:
        raise Bail("pmaports has uncommitted changes", EX_FAIL,
                   "commit or stash them first (they would follow you onto the "
                   "new branch), or pass --allow-dirty if that is what you want")

    _, current, _ = git(pmaports, "rev-parse", "--abbrev-ref", "HEAD")
    base = args.base or _channel_branch(ctx, pmaports) or current

    rc, _, err = git(pmaports, "rev-parse", "--verify", "--quiet", base)
    if rc != 0:
        raise Bail(f"no such base branch: {base}", EX_FAIL,
                   "pass --base, or `git -C <pmaports> fetch` first")

    if not args.yes:
        ctx.out.heading(f"branch {topic} from {base}")
        ctx.out(f"  in {pmaports}")
        ctx.out.blank()
        ctx.out.hint(f"porthole aports start {topic} --yes")
        return EX_OK

    git(pmaports, "switch", "-c", topic, base, check=True)
    ctx.out(ctx.out.paint(f"  on {topic} (from {base})", "green"))
    ctx.out.blank()
    ctx.out.hint("porthole aports status")
    ctx.out.hint("pmbootstrap checksum <pkg>   after editing an APKBUILD's sources")
    return EX_OK


def _channel_branch(ctx, pmaports) -> str:
    """The pmaports branch this channel is supposed to be on.

    Branching a fix off whatever happened to be checked out is how a change
    aimed at edge ends up based on a stable release.

    Two things this gets right that the previous version did not.

    The channel is read from pmaports.cfg, not from `pmbootstrap config
    channel` -- that key is gone in 3.x, so the old call spent 150ms on a
    subprocess whose only possible answer was an argparse error, and every
    caller then silently fell back to `origin/master`.

    And the REMOTE-tracking ref is preferred over a local branch of the same
    name. Nothing updates the local trunk, so on a working checkout
    `master..HEAD` spanned 1676 commits touching 2054 directories -- all of
    upstream since March -- where `origin/main..HEAD` is the 226 commits
    this port actually wrote. A base that wrong does not fail. It answers,
    which is worse: `aports patch` would have written 1676 patch files and
    called them your series.
    """
    import porthole_cmd_channel as channel

    name = channel.current(ctx.cfg)
    branch = pmap.channels(pmaports).get(name, {}).get("branch_pmaports", "")
    candidates = [f"origin/{branch}", branch] if branch else []
    for ref in candidates + ["origin/HEAD"]:
        rc, _, _ = git(pmaports, "rev-parse", "--verify", "--quiet", ref)
        if rc == 0:
            return ref
    return ""


# -------------------------------------------------------------------- diff --

def cmd_diff(args, ctx, pmaports) -> int:
    argv = ["diff"]
    if args.staged:
        argv.append("--staged")
    if args.stat:
        argv.append("--stat")
    if args.mine:
        paths = _device_paths(ctx, pmaports)
        if not paths:
            raise Bail("could not work out which packages are yours", EX_FAIL,
                       "set PORTHOLE_CODENAME in the profile")
        argv += ["--", *paths]
    rc, out, err = git(pmaports, *argv, timeout=120)
    if err:
        ctx.out.warn(err)
    if not out.strip():
        ctx.out("no changes." if not args.mine
                else "no changes to your device's packages.")
        return EX_OK
    print(out)
    return EX_OK


# ------------------------------------------------------------------- patch --

def cmd_patch(args, ctx, pmaports) -> int:
    """Produce a patch series suitable for a pmaports merge request."""
    base = args.base or _channel_branch(ctx, pmaports) or "origin/master"
    rc, _, _ = git(pmaports, "rev-parse", "--verify", "--quiet", base)
    if rc != 0:
        raise Bail(f"no such base: {base}", EX_FAIL,
                   "pass --base with the branch you diverged from")

    _, count, _ = git(pmaports, "rev-list", "--count", f"{base}..HEAD")
    n = int(count or 0)
    if n == 0:
        raise Bail(f"no commits between {base} and HEAD", EX_FAIL,
                   "commit your work first — `porthole aports status`")

    outdir = pathlib.Path(args.out or (ctx.root / ".run" / "aports-patches"))
    outdir.mkdir(parents=True, exist_ok=True)

    argv = ["format-patch", f"{base}..HEAD", "-o", str(outdir)]
    if n > 1:
        argv.append("--cover-letter")
    rc, out, err = git(pmaports, *argv, timeout=120)
    if rc != 0:
        raise Bail(f"format-patch failed: {err}", EX_FAIL)

    files = [pathlib.Path(l) for l in out.splitlines() if l.strip()]
    checks = _lint(pmaports, base, ctx)

    payload = {"base": base, "commits": n,
               "files": [str(f) for f in files], "checks": checks}

    def render():
        o = ctx.out
        o.heading(f"{n} commit(s) since {base}")
        for f in files:
            o(f"  {f}")
        o.blank()
        if checks:
            o.heading("before you send this")
            for status, text in checks:
                colour = {"ok": "green", "warn": "yellow", "fail": "red"}[status]
                o(f"  {o.paint(o.sym('•', '-'), colour)} {text}")
            o.blank()
        o("pmaports takes merge requests on GitLab, not mailed patches — these\n"
          "files are for review and for carrying a change between machines.")
        o.hint("https://gitlab.postmarketos.org/postmarketOS/pmaports")

    return ctx.emit(payload, render)


def _authorship(pmaports, base) -> list:
    """Per-commit author vs sign-off, and cherry-picks that lost their origin.

    The old check tested whether the WORD "Signed-off-by" appeared anywhere in
    the range. taimen's own audit found all 41 of its patches attributed to one
    author -- including Caleb Connolly's and Yassine Oudjana's work -- and this
    check passed the whole series. A substring test cannot see who wrote what.

    Getting this wrong is not a style problem. Sending someone else's patch
    under your name is the highest-severity mistake either port made, and it is
    invisible in a diff.
    """
    findings = []
    sep = "\x1e"
    fmt = f"%H{sep}%an <%ae>{sep}%s{sep}%b\x1d"
    _, log, _ = git(pmaports, "log", f"--format={fmt}", f"{base}..HEAD")
    commits = [c for c in log.split("\x1d") if c.strip()]
    if not commits:
        return findings

    unsigned, mismatched, picked = [], [], []
    for entry in commits:
        # strip("\n"), not strip(): \x1e is whitespace to Python, so a bare
        # .strip() ate the final separator whenever the body was empty -- and
        # an empty body is exactly the unsigned commit this is looking for.
        parts = entry.strip("\n").split(sep)
        parts += [""] * (4 - len(parts))
        sha, author, subject, body = parts[0][:8], parts[1], parts[2], parts[3]
        if not sha:
            continue
        signers = re.findall(r"^\s*Signed-off-by:\s*(.+)$", body, re.M | re.I)
        if not signers:
            unsigned.append(f"{sha} {subject[:48]}")
            continue
        # The AUTHOR must be among the signers. A committer may add their own
        # sign-off on top; what must never happen is a series where one person
        # signed off on work git records as somebody else's, with no trace of
        # the original author.
        norm = author.strip().lower()
        if not any(norm == s.strip().lower() for s in signers):
            mismatched.append(f"{sha} author {author} signed by "
                              f"{', '.join(signers)[:60]}")
        # A cherry-pick that lost `-x` has no record of where it came from.
        if re.search(r"^\s*\(cherry picked from", body, re.M):
            continue
        if re.search(r"\bcherry[- ]pick", subject, re.I):
            picked.append(f"{sha} {subject[:48]}")

    if unsigned:
        findings.append(("warn", f"{len(unsigned)} commit(s) with no "
                                 f"Signed-off-by: {unsigned[0]}"))
    if mismatched:
        findings.append(("fail", f"{len(mismatched)} commit(s) whose AUTHOR is "
                                 f"not among the sign-offs — sending someone "
                                 f"else's work under your name: "
                                 f"{mismatched[0]}"))
    if picked:
        findings.append(("warn", f"{len(picked)} cherry-pick(s) with no "
                                 f"'(cherry picked from ...)' line — the origin "
                                 f"is unrecoverable: {picked[0]}"))
    if not (unsigned or mismatched or picked):
        findings.append(("ok", f"{len(commits)} commit(s): every author is "
                               f"among its own sign-offs"))
    return findings


def _lint(pmaports, base, ctx) -> list[tuple[str, str]]:
    """Cheap checks that catch what pmaports review always catches."""
    out: list[tuple[str, str]] = []

    _, subjects, _ = git(pmaports, "log", "--format=%s", f"{base}..HEAD")
    for subject in subjects.splitlines():
        if not subject.strip():
            continue
        # pmaports convention: `<pkgname>: <what>` -- see its COMMITSTYLE.md.
        if ":" not in subject:
            out.append(("fail", f"commit subject has no `pkg: ` prefix: "
                                f"{subject[:60]!r}"))
        elif len(subject) > 72:
            out.append(("warn", f"subject over 72 chars: {subject[:50]}..."))

    out += _authorship(pmaports, base)

    _, files, _ = git(pmaports, "diff", "--name-only", f"{base}..HEAD")
    changed = files.splitlines()
    if any(f.endswith("APKBUILD") for f in changed):
        out.append(("warn", "an APKBUILD changed — did you bump pkgrel and run "
                            "`pmbootstrap checksum <pkg>`?"))
    if any("/deviceinfo" in f for f in changed):
        out.append(("warn", "deviceinfo changed — verify flash offsets against "
                            "the device before anyone flashes this"))
    if not out:
        out.append(("ok", "subjects and trailers look conventional"))
    return out


# ------------------------------------------------------------- pmbootstrap --

# pmbootstrap subcommands that touch the shared buildroot. `checksum` is on
# this list because it is what destroyed a running kernel build on the redfin
# port: it removed /pmb/chroot_native/home/pmos/build underneath an active
# compile, and the build then failed naming a missing kernel source file. The
# two commands look completely independent from the outside, which is exactly
# why the lock has to be here rather than in the caller's head.
MUTATES_BUILDROOT = ("build", "checksum", "pkgrel_bump", "install", "zap")


def pmb(ctx, *args, timeout=1800, capture=False):
    """Run pmbootstrap, streaming its output by default.

    Streaming matters: a build is minutes long, and a progress bar you cannot
    see is indistinguishable from a hang. `-y` is passed because every caller
    here has already taken its own confirmation via --yes, and asking twice
    trains people to stop reading prompts.

    Anything that touches the buildroot takes the buildroot mutex first, for
    the reason MUTATES_BUILDROOT gives.
    """
    import porthole_buildroot as buildroot

    cmd = ["pmbootstrap", "-y", *args]
    ctx.out(ctx.out.paint(f"  $ {' '.join(cmd)}", "grey"))
    verb = next((a for a in args if not a.startswith("-")), "")
    if verb in MUTATES_BUILDROOT:
        # The workdir the command will ACTUALLY run against, not the host's.
        # `_pmb_argv` routes into the workspace, whose pmbootstrap keeps its
        # own work dir -- so locking the host path meant `aports checksum`
        # and a container build took two different lock FILES for one
        # buildroot, and neither could see the other. Both files exist on a
        # machine that has ever done both. The mutex was two mutexes.
        import porthole_cmd_build as build

        usable, _ = build._workspace_usable(ctx)
        workdir = build.pmb_workdir(ctx, usable)
        with buildroot.hold(workdir, f"aports {verb}",
                            probe=lambda: buildroot.running_build(ctx, True)):
            return _pmb_run(ctx, cmd, timeout, capture)
    return _pmb_run(ctx, cmd, timeout, capture)


def _pmb_argv(cmd, usable: bool, same_tree: bool) -> list[str]:
    """pmbootstrap, run where the buildroot it needs actually is. Pure.

    The HOST pmbootstrap wants a sudo the rootless setup deliberately does not
    have, so every verb in here that shelled out to it failed on a machine
    where the identical call inside the workspace succeeds. `aports build` was
    moved to `porthole pkg` for exactly this reason and the rest were left
    behind, so `aports checksum` failed on a correct APKBUILD and blamed the
    APKBUILD (#25). Routing in ONE place is what stops the next verb inheriting
    it.

    `same_tree` is not paranoia. The workspace mounts the work dir's own
    pmaports at /pmb/cache_git/pmaports and pmbootstrap in there can see no
    other, while the host may be pointed at a per-device worktree by
    PORTHOLE_PMAPORTS. Routing then checksums a DIFFERENT checkout from the one
    the verb just edited and reports success -- the wrong-aport failure this
    repo has already paid for twice. Different trees: stay on the host, where
    at least the tree is the right one and the error is honest.
    """
    if not (usable and same_tree):
        return list(cmd)
    import porthole_cmd_sandbox as sandbox

    return sandbox._exec_argv(
        [" ".join(shlex.quote(a) for a in cmd)], tty=False)


def _same_path(a, b) -> bool:
    """Same directory, symlinks and `..` resolved. A missing path resolves
    fine and simply is not equal to anything that exists."""
    try:
        return pathlib.Path(a).resolve() == pathlib.Path(b).resolve()
    except OSError:
        return False


def _mounted_pmaports(cfg) -> pathlib.Path:
    """The pmaports the workspace can see -- `porthole sandbox up` mounts this
    path and only this one (porthole_cmd_sandbox._up)."""
    return pathlib.Path(cfg.get("PORTHOLE_PMB_DIR") or
                        pathlib.Path.home() / ".local/var/pmbootstrap"
                        ).expanduser() / "cache_git" / "pmaports"


def _pmb_run(ctx, cmd, timeout, capture):
    import porthole_cmd_build as build

    usable, why_not = build._workspace_usable(ctx)
    host_tree = pmap.find_pmaports(ctx.cfg)
    mounted = _mounted_pmaports(ctx.cfg)
    same_tree = bool(host_tree) and _same_path(host_tree, mounted)
    routed = usable and same_tree
    # WHERE it ran is the first thing you need when it fails in a way that
    # makes no sense -- the same reason the build verb says it in cyan.
    if routed:
        ctx.out(ctx.out.paint("  in the workspace (container)", "cyan"))
    elif usable:
        ctx.out(ctx.out.paint(
            f"  on the host: the workspace sees {mounted}, "
            f"not {host_tree}", "yellow"))
    else:
        ctx.out(ctx.out.paint(f"  on the host ({why_not})", "grey"))
    argv = _pmb_argv(cmd, usable, same_tree)
    try:
        if capture:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr
        return subprocess.run(argv, timeout=timeout).returncode, "", ""
    except FileNotFoundError:
        if routed:
            raise Bail("podman is not installed", EX_FAIL,
                       "`porthole doctor` names how to install it") from None
        raise Bail("pmbootstrap is not installed", EX_FAIL,
                   "pipx install pmbootstrap   (`porthole doctor` checks this)"
                   ) from None
    except subprocess.TimeoutExpired:
        raise Bail(f"pmbootstrap timed out after {timeout}s", EX_FAIL) from None


def _pkg_dir(pmaports, name):
    """Where a package lives, or None. pmaports nests packages by category."""
    for pattern in (f"*/*/{name}", f"*/{name}"):
        for path in pmaports.glob(pattern):
            if (path / "APKBUILD").is_file():
                return path
    return None


def _resolve_pkgs(args, ctx, pmaports) -> list[str]:
    """Packages named on the command line, or this device's if none were."""
    if getattr(args, "name", None):
        return [args.name]
    mine = [pathlib.Path(p).name for p in _device_paths(ctx, pmaports)]
    if not mine:
        raise Bail("name a package", EX_USAGE,
                   "or set PORTHOLE_CODENAME in the profile so I can work out "
                   "which packages are yours")
    return mine


# --------------------------------------------------------------------- new --

DEVICEINFO_TEMPLATE = """\
# Reference: <https://postmarketos.org/deviceinfo>
# Please use double quotes only. You can source this file in shell
# scripts.

deviceinfo_format_version="0"
deviceinfo_name="{name}"
deviceinfo_manufacturer="{vendor}"
deviceinfo_codename="{codename}"
deviceinfo_year="{year}"
deviceinfo_dtb="{dtb}"
deviceinfo_arch="{arch}"

# Device related
deviceinfo_chassis="handset"
deviceinfo_drm="false"

# Bootloader related
deviceinfo_flash_method="{flash_method}"
deviceinfo_generate_bootimg="{generate_bootimg}"
deviceinfo_flash_pagesize="{pagesize}"
deviceinfo_header_version="{header_version}"
"""

APKBUILD_TEMPLATE = """\
# Reference: <https://postmarketos.org/devicepkg>
pkgname={pkgname}
pkgdesc="{name}"
pkgver=0.1
pkgrel=0
url="https://postmarketos.org"
license="MIT"
arch="{arch}"
options="!check !archcheck"
depends="
{depends}"
makedepends="devicepkg-dev"
source="
\tdeviceinfo
\tmodules-initfs
"

build() {{
\tdevicepkg_build $startdir $pkgname
}}

package() {{
\tdevicepkg_package $startdir $pkgname
}}

sha512sums=""
"""

MODULES_INITFS_TEMPLATE = """\
# Modules the initramfs needs before the rootfs exists: the storage
# controller, anything needed to unlock FDE, and USB networking for the
# debug shell. One name per line; '#' comments.
{modules}
"""


def cmd_new(args, ctx, pmaports) -> int:
    """Scaffold a pmaports device package, seeded from the closest sibling.

    `pmbootstrap aportgen` forks ALPINE packages; it has nothing for a device
    nobody has ported. The documented pmOS route is to copy the closest
    existing device package and edit it, which is what this does -- except it
    refuses to carry the other device's identity across, because a deviceinfo
    still naming someone else's phone is the single most common way a new port
    ships something nonsensical.
    """
    codename = args.name or ctx.cfg.get("PORTHOLE_DEVICE", "")
    if not codename:
        raise Bail("name the device", EX_USAGE,
                   "porthole aports new google-cheetah")
    if not re.fullmatch(r"[a-z0-9]+-[a-z0-9-]+", codename):
        raise Bail(f"pmOS codenames are <vendor>-<codename>: {codename!r}",
                   EX_USAGE, "e.g. google-cheetah, oneplus-enchilada")

    vendor = codename.split("-", 1)[0]
    pkgname = f"device-{codename}"
    existing = _pkg_dir(pmaports, pkgname)
    if existing and not args.force:
        raise Bail(f"{pkgname} already exists at "
                   f"{existing.relative_to(pmaports)}", EX_FAIL,
                   "pass --force to overwrite it")

    devices = pmap.load_devices(pmaports)
    soc = args.soc or ctx.cfg.get("PORTHOLE_SOC", "")
    # The profile stores a bare SoC ("gs201"); pmaports names it vendor-first
    # ("google-gs201"). Accept either rather than making the user remember.
    if soc and not any(d.soc == soc for d in devices):
        for cand in (f"{vendor}-{soc}", f"qcom-{soc}"):
            if any(d.soc == cand for d in devices):
                soc = cand
                break
    sibs = pmap.siblings(devices, soc, exclude=codename) if soc else []
    sibling = sibs[0] if sibs else None

    cfg = ctx.cfg
    info = dict(sibling.info) if sibling else {}
    fields = {
        "codename": codename,
        "vendor": cfg.get("PORTHOLE_VENDOR") or vendor.capitalize(),
        "name": cfg.get("PORTHOLE_DEVICE_NAME") or codename,
        "year": cfg.get("PORTHOLE_YEAR") or "",
        "arch": cfg.get("PORTHOLE_ARCH") or info.get("arch", "aarch64"),
        "dtb": cfg.get("PORTHOLE_DTB") or "",
        # Platform-shaped values CAN come from a sibling: how images reach the
        # device is a property of the SoC's bootloader, not of the board.
        "flash_method": info.get("flash_method", "fastboot"),
        "generate_bootimg": info.get("generate_bootimg", "true"),
        "pagesize": cfg.get("PORTHOLE_BOOTIMG_PAGESIZE")
                    or info.get("flash_pagesize", ""),
        "header_version": cfg.get("PORTHOLE_BOOTIMG_HEADER_VERSION")
                          or info.get("header_version", ""),
    }

    category = args.category or "testing"
    dest = pmaports / "device" / category / pkgname

    depends = ["\tpostmarketos-base"]
    if soc:
        depends.append(f"\tsoc-{soc}")
        depends.append(args.kernel and f"\t{args.kernel}"
                       or f"\tlinux-postmarketos-{soc.split('-', 1)[-1]}")
    elif args.kernel:
        depends.append(f"\t{args.kernel}")

    modules = "\n".join(args.module or []) or (
        "# TODO: the storage driver belongs here. Without it the rootfs never\n"
        "# appears and the boot looks exactly like a kernel hang.")

    files = {
        "deviceinfo": DEVICEINFO_TEMPLATE.format(**fields),
        "APKBUILD": APKBUILD_TEMPLATE.format(
            pkgname=pkgname, name=fields["name"], arch=fields["arch"],
            depends="".join(d + "\n" for d in sorted(depends))),
        "modules-initfs": MODULES_INITFS_TEMPLATE.format(modules=modules),
    }
    blanks = [k for k, v in fields.items() if not v]

    if not args.yes:
        o = ctx.out
        o.heading(f"would create device/{category}/{pkgname}")
        for name in sorted(files):
            o(f"  {name}")
        o.blank()
        if sibling:
            o(f"  seeded from {o.paint(sibling.codename, 'bold')} "
              f"({sibling.category}) on {soc}")
        else:
            o(o.paint(f"  no sibling on {soc or 'an unknown SoC'} — nothing "
                      f"seeded", "yellow"))
        if blanks:
            o(o.paint(f"  {len(blanks)} field(s) left blank: "
                      f"{', '.join(blanks)}", "yellow"))
        o.blank()
        o.hint(f"porthole aports new {codename} --yes")
        return EX_OK

    dest.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (dest / name).write_text(body)

    payload = {"package": pkgname, "path": str(dest), "category": category,
               "seeded_from": sibling.codename if sibling else "",
               "soc": soc, "files": sorted(files), "blank_fields": blanks}

    def render():
        o = ctx.out
        o(f"{o.paint(o.sym('✓', 'ok'), 'green')} created "
          f"device/{category}/{pkgname}")
        for name in sorted(files):
            o(f"    {name}")
        if sibling:
            o(f"  seeded from {sibling.codename} on {soc}")
        else:
            o(o.paint("  no sibling seeding — every value is yours", "yellow"))
        if blanks:
            o(o.paint(f"  still blank: {', '.join(blanks)}", "yellow"))
        o.blank()
        o.heading("next, in this order")
        o.hint(f"porthole aports lint {pkgname}")
        o.hint(f"porthole aports checksum {pkgname}")
        o.hint(f"porthole aports build {pkgname}")
        o.blank()
        o(o.paint("  Blank deviceinfo values are deliberate. A wrong flash "
                  "offset makes a\n  device that does not boot and says "
                  "nothing about why.", "grey"))

    return ctx.emit(payload, render)


# ------------------------------------------------- checksum / build / lint --

def _series_problems(pkg_dir: pathlib.Path) -> list:
    """Patch files on disk that the APKBUILD does not list, and duplicate
    numbers among the ones it does.

    Both are silent by construction: `pmbootstrap checksum` validates every
    LISTED source and says nothing about a file sitting next to them, so a
    regenerated series whose names changed leaves the old patch orphaned and
    the new one unbuilt. Paid for on taimen 2026-08-26 -- two kernels were
    built, flashed and tested without the change they were supposed to carry.
    """
    apkbuild = pkg_dir / "APKBUILD"
    if not apkbuild.is_file():
        return []

    text = apkbuild.read_text()
    listed = set(re.findall(r"^\s*(\d{4}-\S+?\.patch)\s*$", text, re.M))
    on_disk = {p.name for p in pkg_dir.glob("[0-9][0-9][0-9][0-9]-*.patch")}

    problems = []
    for orphan in sorted(on_disk - listed):
        problems.append(("orphan", f"{orphan} is not listed in the APKBUILD"))
    for missing in sorted(listed - on_disk):
        problems.append(("missing", f"{missing} is listed but not on disk"))

    seen = {}
    for name in sorted(listed):
        seen.setdefault(name[:4], []).append(name)
    for num, names in sorted(seen.items()):
        if len(names) > 1:
            problems.append(("duplicate",
                             f"{num} is used by {len(names)} patches: "
                             + ", ".join(names)))

    # The series may be complete and correctly listed and STILL not apply.
    # taimen's 0199 was listed, on disk, uniquely numbered -- and one
    # insertion short of its own hunk header, so `patch` refused the whole
    # series and no aport kernel could build. See lib/porthole_series.py for
    # why this is arithmetic rather than `patch --dry-run`.
    for name in sorted(listed & on_disk):
        try:
            text = (pkg_dir / name).read_text(errors="replace")
        except OSError:
            continue
        for kind, message in porthole_series.scan(text):
            problems.append((kind, "{}: {}".format(name, message)))

    return problems


def cmd_checksum(args, ctx, pmaports) -> int:
    """`pmbootstrap checksum` -- mandatory after touching any listed source."""
    for pkg in _resolve_pkgs(args, ctx, pmaports) if not args.changed else []:
        problems = _series_problems(_pkg_dir(pmaports, pkg))
        # `duplicate` is untidy and `stripped` is damage that GNU patch
        # usually still applies -- two ACTIVE pmaports patches carry it and
        # build. Neither stops a build. `orphan`, `missing` and `malformed`
        # all change what gets built, and that is what has to stop one.
        fatal = [x for x in problems if x[0] not in ("duplicate", "stripped")]
        for kind, msg in problems:
            ctx.out(ctx.out.paint(f"  {kind:<9} {msg}",
                                  "red" if kind != "duplicate" else "yellow"))
        if fatal:
            raise Bail(f"{pkg}: the patch series and the APKBUILD disagree",
                       EX_FAIL,
                       "checksum only validates what is LISTED, so this would "
                       "otherwise build without the patch you just wrote")

    if args.changed:
        rc, _, _ = pmb(ctx, "checksum", "--changed", timeout=900)
    else:
        rc, _, _ = pmb(ctx, "checksum", *_resolve_pkgs(args, ctx, pmaports),
                       timeout=900)
    if rc != 0:
        # NOT "a source could not be fetched". That was a guess dressed as a
        # diagnosis, and on a rootless host -- where the real cause was
        # pmbootstrap wanting sudo -- it sent a session chasing a URL problem
        # that did not exist. pmbootstrap streams its own error; point at it.
        raise Bail(f"checksum failed -- pmbootstrap exited {rc}", EX_FAIL,
                   "its error is above; porthole does not know the cause and "
                   "will not invent one")
    ctx.out(ctx.out.paint("  checksums updated", "green"))
    for name in _resolve_pkgs(args, ctx, pmaports):
        directory = _pkg_dir(pmaports, name)
        if directory is None:
            continue
        stray = untracked_patches(
            directory, (directory / "APKBUILD").read_text(errors="replace"))
        if stray:
            ctx.out(ctx.out.paint(
                f"  {name}: {', '.join(stray)} sit beside the APKBUILD but are "
                f"not in source=, so they get no checksum and will not apply",
                "yellow"))
            ctx.out(ctx.out.paint(
                f"  porthole aports patch {name}   adds them properly", "cyan"))
    return EX_OK


def cmd_build(args, ctx, pmaports) -> int:
    """Build a package -- through `porthole pkg`, which is the one
    implementation.

    This used to call pmbootstrap directly, which meant `aports build` had no
    progress bar, no --lax handling in the workspace, no artifact
    verification and no way to detach. Two doors where one is worse is how the
    redfin port ended up using neither and hand-rolling pmbootstrap instead.
    """
    import argparse

    import porthole_cmd_pkg as pkgverb

    rc = EX_OK
    for name in _resolve_pkgs(args, ctx, pmaports):
        forwarded = argparse.Namespace(
            target=name, arch=getattr(args, "arch", None),
            timeout=getattr(args, "timeout", pkgverb.DEFAULT_TIMEOUT),
            verbose=False, dry_run=False, detach=False, wait=0.0,
            json=getattr(args, "json", False),
            force=getattr(args, "force", False))
        rc = pkgverb._build(ctx, forwarded)
        if rc != EX_OK:
            return rc
    return rc


def _lint_unavailable(reason: str) -> str:
    """What to say when pmbootstrap cannot lint at all.

    Separated so a test can assert the wording never drifts back into
    sounding like a finding about the user's packages.
    """
    return (f"{reason}. apkbuild-lint moved out of pmbootstrap; pmaports CI "
            f"still runs it, so this is a gap in local checking rather than "
            f"a problem with your package")


def lint_verdict(problems, apkbuild_lint_rc) -> int:
    """The exit code for a lint run. Pure, so the 1/69 split is testable.

    `apkbuild_lint_rc` is None when the subcommand is absent.

    The split is the contract, not a detail: 1 means the measurement says no,
    69 means no measurement happened. `aports lint` used to report "lint found
    problems" for a subcommand pmbootstrap had removed, which is exit 1 lying
    about the user's packages -- the exact confusion AGENTS.md section 6
    forbids.
    """
    if any(kind not in ("duplicate", "stripped") for kind, _ in problems):
        return EX_FAIL
    if apkbuild_lint_rc is None:
        return EX_UNAVAILABLE
    return EX_FAIL if apkbuild_lint_rc != 0 else EX_OK


def _lint_unavailable_hint(problems) -> str:
    """The Bail hint for a missing apkbuild-lint. Pure, so the "clean" claim
    can be asserted without a ctx -- same reason lint_verdict is separate.

    Must not say "clean" when non-fatal warnings were just printed two lines
    above in the same invocation; that is the printed claim outrunning the
    measurement that AGENTS.md section 6 forbids.
    """
    state = "found only non-fatal issues" if problems else "ran and was clean"
    return f"the series check above {state}; pmaports CI lints the merge request"


def cmd_lint(args, ctx, pmaports) -> int:
    """The local series check always; apkbuild-lint when pmbootstrap has it.

    Two checks with two provenances, reported separately. The series check is
    ours and always runs. apkbuild-lint is pmbootstrap's, moved out in 3.11.1,
    and its absence is a gap in local checking rather than a problem with
    anyone's package -- pmaports CI still runs it.
    """
    import porthole_pmb_api as pmb_api

    names = _resolve_pkgs(args, ctx, pmaports)
    problems = []
    for name in names:
        directory = _pkg_dir(pmaports, name)
        if directory is None:
            continue
        for kind, message in _series_problems(directory):
            problems.append((kind, "{}: {}".format(name, message)))

    for kind, message in problems:
        colour = "yellow" if kind in ("duplicate", "stripped") else "red"
        ctx.out(ctx.out.paint("  {:<9} {}".format(kind, message), colour))
    if not problems:
        ctx.out(ctx.out.paint("  series clean", "green"))

    gone = pmb_api.missing("subcommands", "lint")
    rc = None
    if gone:
        ctx.out(ctx.out.paint("  " + _lint_unavailable(gone), "grey"))
    else:
        rc, _, _ = pmb(ctx, "lint", *names, timeout=900)
        ctx.out(ctx.out.paint(
            "  apkbuild-lint clean" if rc == 0 else "  apkbuild-lint found problems",
            "green" if rc == 0 else "red"))

    verdict = lint_verdict(problems, rc)
    if verdict == EX_UNAVAILABLE:
        raise Bail(_lint_unavailable(gone), EX_UNAVAILABLE,
                   _lint_unavailable_hint(problems))
    if verdict != EX_OK:
        raise Bail("lint found problems", EX_FAIL,
                   "fix them before opening a merge request — pmaports CI "
                   "runs this too")
    return EX_OK


def cmd_ci(args, ctx, pmaports) -> int:
    """pmaports' own CI, locally, from inside the checkout."""
    cmd = ["pmbootstrap", "-y", "ci"] + (["--fast"] if args.fast else [])
    ctx.out(ctx.out.paint(f"  $ (cd {pmaports} && {' '.join(cmd)})", "grey"))
    try:
        rc = subprocess.run(cmd, cwd=str(pmaports),
                            timeout=args.timeout).returncode
    except FileNotFoundError:
        raise Bail("pmbootstrap is not installed", EX_FAIL) from None
    except subprocess.TimeoutExpired:
        raise Bail(f"ci timed out after {args.timeout}s", EX_FAIL) from None
    if rc != 0:
        raise Bail("pmaports CI failed", EX_FAIL,
                   "this is what your merge request will run — fix it here")
    ctx.out(ctx.out.paint("  CI clean", "green"))
    return EX_OK


def cmd_bump(args, ctx, pmaports) -> int:
    """pkgrel_bump: tell the builders a package must be rebuilt."""
    rc, _, _ = pmb(ctx, "pkgrel_bump", *_resolve_pkgs(args, ctx, pmaports),
                   timeout=600)
    if rc != 0:
        raise Bail("pkgrel_bump failed", EX_FAIL)
    return EX_OK


# ----------------------------------------------------------------- patches --

def dropped_patches(listed, new) -> list:
    """Patches in source= that this rewrite would not reproduce. Pure.

    `cmd_patches` deletes every .patch and rewrites source= from the tree, so
    running it against a tree that lacks work the aport carries removes that
    work silently. Against the taimen aport from a tree without the venus
    commits it would have deleted all 19 venus patches -- and the failure mode
    of a missing venus patch is a missing /dev node and nothing in any log.
    """
    fresh = set(new)
    return [name for name in listed if name not in fresh]


def next_patch_number(existing) -> int:
    """One past the highest 4-digit prefix in the series. Pure."""
    numbers = []
    for name in existing:
        head = name[:4]
        if head.isdigit():
            numbers.append(int(head))
    return (max(numbers) + 1) if numbers else 1


def cmd_patches(args, ctx, pmaports) -> int:
    """Export kernel commits into a kernel aport as a numbered patch series.

    This is the loop that hurts. You have a kernel tree with commits on top of
    an upstream base; the aport wants them as `.patch` files listed in
    `source=`, with checksums regenerated. By hand that is format-patch, copy,
    hand-edit a shell array, forget the checksum, and find out twenty minutes
    into a build.

    Stale patches are REMOVED rather than merged: the series in the aport must
    equal the series in the tree. A patch dropped from the branch but left in
    the package is a change nobody can account for, and it will be built.

    That deletion refuses to run when the tree does not reproduce a patch the
    aport currently carries -- a tree missing work the aport has looks
    identical to a tree the work was deliberately dropped from, and the
    former used to get silently deleted. `--drop` overrides it when a
    regenerated series really is what you mean. `--append` is for the common
    case instead: N new commits on top of an existing series, numbered from
    the end, nothing deleted.
    """
    tree = pathlib.Path(
        args.tree or ctx.cfg.get("PORTHOLE_WORKDIR", "") or ".").expanduser()
    if not (tree / ".git").exists():
        if (tree / "linux" / ".git").exists():
            tree = tree / "linux"
        else:
            raise Bail(f"{tree} is not a git checkout", EX_FAIL,
                       "pass --tree <kernel repo>, or set PORTHOLE_WORKDIR")

    if not args.base:
        raise Bail("name the upstream base", EX_USAGE,
                   "porthole aports patches --base v6.18 --pkg "
                   "linux-postmarketos-gs201")
    base = args.base

    pkgname = args.pkg or ctx.cfg.get("PORTHOLE_KERNEL_PKG", "")
    if not pkgname:
        raise Bail("which kernel package?", EX_USAGE,
                   "pass --pkg, or set PORTHOLE_KERNEL_PKG in the profile")
    pkgdir = _pkg_dir(pmaports, pkgname)
    if not pkgdir:
        raise Bail(f"no package {pkgname!r} in pmaports", EX_FAIL,
                   "porthole aports new   creates a device package; a kernel "
                   "package is copied from the closest sibling's")

    rc, _, _ = git(tree, "rev-parse", "--verify", "--quiet", base)
    if rc != 0:
        raise Bail(f"no such base in the kernel tree: {base}", EX_FAIL,
                   "fetch the tag first, or name a commit that exists")
    _, count, _ = git(tree, "rev-list", "--count", f"{base}..HEAD")
    n = int(count or 0)
    if n == 0:
        raise Bail(f"no commits between {base} and HEAD in {tree}", EX_FAIL,
                   "commit your kernel work first")

    old = sorted(p.name for p in pkgdir.glob("*.patch"))

    if not args.yes:
        _, subjects, _ = git(tree, "log", "--format=%s", "--reverse",
                             f"{base}..HEAD")
        o = ctx.out
        o.heading(f"{n} commit(s) in {tree.name} since {base}")
        for i, subject in enumerate(subjects.splitlines(), 1):
            o(f"  {i:04d}  {subject[:70]}")
        o.blank()
        o(f"  into {pkgdir.relative_to(pmaports)}")
        if old:
            if args.append:
                o(o.paint(f"  appending after {len(old)} existing patch(es)",
                          "yellow"))
            else:
                o(o.paint(f"  replacing {len(old)} existing patch(es)", "yellow"))
        o.blank()
        extra = " --append" if args.append else ""
        o.hint(f"porthole aports patches --base {base} --pkg {pkgname}"
               f"{extra} --yes")
        return EX_OK

    format_patch_args = ["format-patch", f"{base}..HEAD", "-o", str(pkgdir),
                         "--no-signature", "--zero-commit", "--no-numbered"]
    if args.append:
        format_patch_args += ["--start-number", str(next_patch_number(old))]
    # --zero-commit and --no-signature keep the patch files stable across
    # regenerations, so re-running this does not produce a diff of noise.
    rc, out, err = git(tree, *format_patch_args, timeout=300)
    if rc != 0:
        raise Bail(f"format-patch failed: {err}", EX_FAIL)
    new = sorted(pathlib.Path(l).name for l in out.splitlines() if l.strip())

    gone = dropped_patches(old, new)
    if gone and not args.append and not args.drop:
        raise Bail(
            f"this would delete {len(gone)} patch(es) the tree does not "
            f"reproduce", EX_FAIL,
            "they are: " + ", ".join(gone[:5])
            + (" ..." if len(gone) > 5 else "")
            + ".  If that is what you meant, add --drop. If you have N NEW "
              "commits on top, use --append instead — it renumbers from the "
              "end and deletes nothing.")

    if args.append:
        series = old + new
    else:
        for name in gone:
            (pkgdir / name).unlink()
        series = new

    changed = _rewrite_source(pkgdir / "APKBUILD", series)
    ctx.out(f"  {len(new)} patch(es) written to {pkgdir.relative_to(pmaports)}")
    if changed:
        ctx.out("  APKBUILD source= updated")

    rc, _, _ = pmb(ctx, "checksum", pkgname, timeout=900)
    if rc != 0:
        raise Bail("checksum failed after writing patches", EX_FAIL,
                   "the patches are on disk; fix the APKBUILD, then "
                   "`porthole aports checksum`")

    payload = {"package": pkgname, "tree": str(tree), "base": base,
               "patches": series, "removed": [] if args.append else gone}

    def render():
        o = ctx.out
        o.blank()
        o(f"{o.paint(o.sym('✓', 'ok'), 'green')} {pkgname}: {len(new)} "
          f"patch(es), checksums regenerated")
        o.blank()
        o.hint(f"porthole aports build {pkgname} --force")
        o.hint("porthole aports lint")
        o.hint("porthole aports patch      when it is ready to send")

    return ctx.emit(payload, render)


SOURCE_BLOCK = re.compile(r'^(source=")(.*?)(")', re.M | re.S)


def untracked_patches(directory, apkbuild_text: str) -> list:
    """`.patch` files beside the APKBUILD that `source=` does not list.

    pmbootstrap checksums what is in source=, so a patch listed only in
    patches= silently gets no checksum and the build fails later complaining
    about something else. `porthole aports patch` has always handled this
    correctly; the redfin session did it by hand because nothing said so.
    """
    match = SOURCE_BLOCK.search(apkbuild_text)
    listed = set(match.group(2).split()) if match else set()
    return sorted(path.name for path in directory.glob("*.patch")
                  if path.name not in listed)


def _rewrite_source(apkbuild: pathlib.Path, patches: list[str]) -> bool:
    """Replace the .patch entries in source= with exactly this series.

    Non-patch entries -- the tarball, the config -- are preserved in their
    original order. pmaports convention is sources first and patches after, and
    a rewrite that shuffled them would produce a diff nobody wants to review.
    """
    try:
        text = apkbuild.read_text()
    except OSError:
        return False
    m = SOURCE_BLOCK.search(text)
    if not m:
        return False
    kept = [e for e in m.group(2).split() if not e.endswith(".patch")]
    body = "\n".join(f"\t{e}" for e in kept + patches)
    updated = text[:m.start()] + f'{m.group(1)}\n{body}\n{m.group(3)}' + text[m.end():]
    if updated == text:
        return False
    apkbuild.write_text(updated)
    return True



# ------------------------------------------------------------- worktree --

def _cache_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache")
    return pathlib.Path(base) / "porthole" / "aports"


def cmd_worktree(args, ctx, pmaports) -> int:
    """Give this device its own checkout of pmaports, on its own branch.

    pmaports is ONE clone that pmbootstrap also writes to, sitting on ONE
    branch. Two devices therefore share it: building for cheetah sees whatever
    taimen left checked out, and a channel switch moves the branch under both.
    `use` could warn about that; it could not fix it.

    A git worktree fixes it properly -- one clone, one working tree per device,
    each on its own branch, no second fetch and no duplicated object store.
    `pmbootstrap -p <path>` then points at the right one, which is how the
    isolation reaches the build rather than stopping at porthole's edge.

    Opt-in, never automatic: this writes into the user's pmbootstrap clone, and
    a tool that reorganises a shared resource behind your back is one you stop
    trusting.
    """
    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    if not device:
        raise Bail("no device selected", EX_USAGE, "porthole use <codename>")

    # Already a worktree? Then pmaports resolved to it and there is nothing to
    # do -- saying so beats a git error about an existing path.
    rc, common, _ = git(pmaports, "rev-parse", "--git-common-dir")
    clone = pmaports
    if common and common not in (".git", str(pmaports / ".git")):
        clone = pathlib.Path(common).parent

    dest = pathlib.Path(args.out).expanduser() if args.out else _cache_dir() / device
    branch = args.name or f"{device.split('-', 1)[-1]}-bringup"
    key = f"PORTHOLE_PMAPORTS_{device.upper().replace('-', '_')}"

    rc, existing, _ = git(clone, "worktree", "list", "--porcelain")
    already = any(line.split(" ", 1)[1] == str(dest)
                  for line in existing.splitlines() if line.startswith("worktree "))
    rc_b, _, _ = git(clone, "rev-parse", "--verify", "--quiet", branch)

    if already and not args.force:
        raise Bail(f"a worktree already exists at {dest}", EX_FAIL,
                   f"porthole config {key}   to see if it is wired up, or pass "
                   f"--force to recreate it")
    if rc_b == 0 and not args.force:
        raise Bail(f"branch {branch!r} already exists in {clone.name}", EX_FAIL,
                   f"pass --name to pick another, or --force to check it out "
                   f"into the new worktree as-is")

    base = args.base or _channel_branch(ctx, clone) or "master"

    if not args.yes:
        o = ctx.out
        o.heading(f"would give {device} its own pmaports checkout")
        o.kv("clone", str(clone), 10)
        o.kv("worktree", str(dest), 10)
        o.kv("branch", f"{branch}  (from {base})", 10)
        o.blank()
        o("  One clone, one working tree per device. Nothing is fetched and no")
        o("  objects are duplicated; only a second working copy is checked out.")
        o.blank()
        o.hint(f"porthole aports worktree --yes")
        return EX_OK

    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = ["worktree", "add"]
    if args.force:
        argv.append("--force")
    if rc_b == 0:
        argv += [str(dest), branch]
    else:
        argv += ["-b", branch, str(dest), base]
    rc, _, err = git(clone, *argv, timeout=300)
    if rc != 0:
        raise Bail(f"git worktree add failed: {err}", EX_FAIL)

    import porthole_cmd_use as use
    use.set_key(use.config_path(), key, str(dest))

    payload = {"device": device, "worktree": str(dest), "branch": branch,
               "base": base, "clone": str(clone), "config_key": key}

    def render():
        o = ctx.out
        o(f"{o.paint(o.sym('✓', 'ok'), 'green')} {device} now has its own "
          f"pmaports checkout")
        o.kv("worktree", str(dest), 10)
        o.kv("branch", f"{branch} (from {base})", 10)
        o.blank()
        o(f"  {key} written to your config, so every porthole and pmbootstrap")
        o(f"  call for {device} uses this tree and not the shared one.")
        o.blank()
        o.hint("porthole aports status")

    return ctx.emit(payload, render)

# ---------------------------------------------------------------- dispatch --

ACTIONS = {"status": cmd_status, "start": cmd_start, "diff": cmd_diff,
           "patch": cmd_patch, "new": cmd_new, "checksum": cmd_checksum,
           "build": cmd_build, "lint": cmd_lint, "ci": cmd_ci,
           "bump": cmd_bump, "patches": cmd_patches,
           "worktree": cmd_worktree}


def dispatch(args, ctx) -> int:
    action = args.action or "status"
    fn = ACTIONS.get(action)
    if not fn:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: {', '.join(ACTIONS)}")
    return fn(args, ctx, _pmaports(ctx))


SPEC = {
    "verb": "aports",
    "order": 42,
    "group": "sources",
    "help": "work on pmaports: status, feature branches, diffs, patches",
    "description": (
        "pmaports is a shared checkout that pmbootstrap also writes to, on a\n"
        "branch a channel switch will move under you. This answers what branch\n"
        "am I on, what have I changed, which of it is my device's, and is it\n"
        "ready to send.\n\n"
        "Everything here is read-only or an ordinary git operation. Nothing\n"
        "rewrites history and nothing pushes."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION",
                      "choices": list(ACTIONS),
                      "help": "status | start | new | worktree | checksum | "
                              "build | lint | ci | bump | patches | diff | patch"}),
        (["name"], {"nargs": "?", "metavar": "NAME",
                    "help": "start: branch name. new: device codename. "
                            "build/lint/checksum/bump: package"}),
        (["--base"], {"metavar": "REF", "help": "branch/patch/patches base"}),
        (["--mine"], {"action": "store_true", "group": "diff",
                      "help": "only your device's packages"}),
        (["--staged"], {"action": "store_true", "group": "diff",
                        "help": "staged changes"}),
        (["--stat"], {"action": "store_true", "group": "diff",
                      "help": "summary only"}),
        (["--out"], {"metavar": "DIR",
                     "help": "patch: output directory. worktree: where to put it"}),
        (["--soc"], {"metavar": "SOC", "group": "new",
                     "help": "seed from the closest sibling on this SoC"}),
        (["--category"], {"metavar": "DIR", "group": "new",
                          "help": "pmaports category (default testing)"}),
        (["--kernel"], {"metavar": "PKG", "group": "new",
                        "help": "the kernel package to depend on"}),
        (["--module"], {"action": "append", "metavar": "NAME", "group": "new",
                        "help": "initramfs module (repeatable)"}),
        (["--changed"], {"action": "store_true", "group": "checksum",
                         "help": "every package with unstaged changes"}),
        (["--arch"], {"metavar": "ARCH", "group": "build",
                      "help": "target architecture"}),
        (["--fast"], {"action": "store_true", "group": "ci",
                      "help": "fast scripts only"}),
        (["--timeout"], {"type": int, "default": 3600, "metavar": "SEC",
                         "help": "build/ci: seconds before giving up"}),
        (["--tree"], {"metavar": "PATH", "group": "patches",
                      "help": "the kernel checkout (default "
                              "$PORTHOLE_WORKDIR)"}),
        (["--pkg"], {"metavar": "PKG", "group": "patches",
                     "help": "the kernel package to write into"}),
        (["--force"], {"action": "store_true",
                       "help": "new: overwrite. build: rebuild anyway"}),
        (["--allow-dirty"], {"action": "store_true", "dest": "allow_dirty",
                             "help": "start: branch despite uncommitted changes"}),
        (["--yes"], {"action": "store_true",
                     "help": "start/new/patches: actually do it"}),
        (["--append"], {"action": "store_true", "group": "patches",
                        "help": "add these commits after the existing "
                                "series instead of replacing it"}),
        (["--drop"], {"action": "store_true", "group": "patches",
                      "help": "allow the rewrite to delete patches "
                              "the tree does not reproduce"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "escapes_scope": True,
    "run": dispatch,
    "examples": [
        "porthole aports status",
        "porthole aports worktree --yes",
        "porthole aports start cheetah-gs201 --yes",
        "porthole aports new google-cheetah --soc google-gs201 --yes",
        "porthole aports checksum --changed",
        "porthole aports build device-google-cheetah --force",
        "porthole aports patches --base v6.18 --pkg linux-postmarketos-gs201 --yes",
        "porthole aports lint",
        "porthole aports diff --mine --stat",
        "porthole aports patch",
    ],
}
