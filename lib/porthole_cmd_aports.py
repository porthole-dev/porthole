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

import pathlib
import re
import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE
import porthole_pmaports as pmap


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
                   "run `pmbootstrap init` once so it clones one, or set "
                   "PORTHOLE_PMAPORTS")
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

    payload = {
        "pmaports": str(pmaports), "branch": branch, "upstream": upstream,
        "ahead": ahead, "behind": behind,
        "changes": changes, "device_paths": mine,
        "device_changes": mine_changed,
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
        o.hint("porthole aports diff            what changed")
        o.hint("porthole aports start <topic>   a branch for a new change")
        o.hint("porthole aports patch           a series ready to send")

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
    if porcelain.strip() and not args.force:
        raise Bail("pmaports has uncommitted changes", EX_FAIL,
                   "commit or stash them first (they would follow you onto the "
                   "new branch), or pass --force if that is what you want")

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
    """
    try:
        proc = subprocess.run(["pmbootstrap", "config", "channel"],
                              capture_output=True, text=True, timeout=20)
        channel = proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return pmap.channels(pmaports).get(channel, {}).get("branch_pmaports", "")


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

    _, bodies, _ = git(pmaports, "log", "--format=%b%n---", f"{base}..HEAD")
    if "Signed-off-by" not in bodies:
        out.append(("warn", "no Signed-off-by trailer — pmaports does not "
                            "require DCO, but your kernel commits do"))

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


# ---------------------------------------------------------------- dispatch --

ACTIONS = {"status": cmd_status, "start": cmd_start,
           "diff": cmd_diff, "patch": cmd_patch}


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
                      "help": "status | start | diff | patch"}),
        (["name"], {"nargs": "?", "help": "start: the topic branch name"}),
        (["--base"], {"metavar": "REF", "help": "branch/patch base"}),
        (["--mine"], {"action": "store_true",
                      "help": "diff: only your device's packages"}),
        (["--staged"], {"action": "store_true", "help": "diff: staged changes"}),
        (["--stat"], {"action": "store_true", "help": "diff: summary only"}),
        (["--out"], {"metavar": "DIR", "help": "patch: output directory"}),
        (["--force"], {"action": "store_true",
                       "help": "start: branch despite uncommitted changes"}),
        (["--yes"], {"action": "store_true", "help": "start: actually do it"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": dispatch,
    "examples": [
        "porthole aports status",
        "porthole aports start taimen-camera --yes",
        "porthole aports diff --mine --stat",
        "porthole aports patch",
    ],
}
