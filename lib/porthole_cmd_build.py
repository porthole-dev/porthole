#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole build` and `porthole flash` -- the loop the toolkit was missing.

The build/flash cycle existed only as `tools/ph-build.sh`, which must be
SOURCED (it defines shell functions and needs envkernel's aliases in the
caller's shell). That made it unreachable from `porthole run`, which executes
tools rather than sourcing them -- so the second half of a port had no verb at
all, and a third device either forked 700 lines of shell or hand-rolled the
envkernel loop from a runbook.

These verbs source it in a subshell and call the function, so the sourced-env
requirement is honoured and the capability becomes addressable: `porthole
build`, `porthole flash`, and therefore also `porthole next`, the TUI, and any
agent reading the verb table.

Flashing is irreversible on the wrong slot, so it goes through the same
confirmation boundary as everything else: `--yes` or nothing happens.
"""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import time
import sys

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE

# Each verb maps to a shell function ph-build.sh defines. The names are kept
# from the taimen toolbox because they are what every runbook prints and what
# people already type interactively.
#
# The ladder, cheapest rung first. Picking the lowest rung that covers your
# change is the single biggest speed lever in this toolbox, and it was
# unreachable: `mod` and `boot` existed only as shell functions nothing in the
# verb table named, so an agent reading `porthole build --help` saw the ~10
# minute rung and used it to iterate on one driver.
#
# `fast` named `tkfast`, which has never existed in ph-build.sh -- the function
# is `tkbuild-kernel`, so the verb failed with "command not found" for every
# caller. tests/test_build_flash.py now asserts every name here is real.
# The descriptions are printed as "would <description>", so they read as verb
# phrases rather than labels.
# `auto` is deliberately NOT in here. ACTIONS maps a rung to a ph-build.sh
# function, and a test asserts every target really exists -- because `fast`
# pointed at a `tkfast` that never existed for the whole life of the verb.
# `auto` has no shell function; it chooses one. Keeping it out leaves that
# invariant absolute instead of adding an exemption a real typo could hide in.
AUTO_DESC = "build the cheapest rung that covers what actually changed"

ACTIONS = {
    "mod": ("tkmod",
            "build one module, push it, reload it and verify -- no reboot (~40s)"),
    "boot": ("tkboot",
             "build the dtb, repack and RAM-boot it -- no pmbootstrap (~40s)"),
    "fast": ("tkbuild-kernel",
             "build the kernel and flash boot only, UUIDs untouched (~6m)"),
    "kernel": ("tkbuild",
               "build the kernel, package it, install and verify, but NOT flash (~10m)"),
    "upgrade": ("tkupgrade-kernel",
                "swap to a DIFFERENT kernel flavor: push modules, flash boot (~7m)"),
    "clean": ("tkclean", "unstack /mnt/linux binds"),
    "purge": ("tkpurge-devpkgs", "remove envkernel apks that outrank a release"),
}

# The rungs that compile and move the device. `clean` and `purge` are neither.
# `auto` is here so its FALLBACK -- no tree yet, or an unfilled profile --
# renders the ladder preview instead of running an empty function name.
BUILD_ACTIONS = ("auto", "mod", "boot", "fast", "kernel", "upgrade")

# What each rung covers, so the preview can say why you would pick another.
# This is the table an agent needs and had no way to get.
# `boot` is DTS-ONLY by default for a reason. Adding --kernel rebuilds Image.gz,
# and a rebuilt kernel will not load the modules already on the device: the
# build id and the BTF move, so every .ko is refused. That is not a CONFIG-change
# hazard as this table said until 2026-08-27 -- it is EVERY --kernel rebuild.
# On a device whose initramfs needs a module to mount root (taimen loop-mounts
# its subpartition, so it needs loop.ko) the RAM boot cannot reach userspace at
# all: it lands in the initramfs debug shell looking like a bad kernel.
LADDER = [
    ("mod", "a driver that is a module -- try this FIRST, even when the device "
     "ships from an aport: MODVERSIONS makes an ABI mismatch a loud refusal",
     "no reboot at all"),
    ("boot", "a DTS change", "one fastboot boot"),
    ("boot --kernel", "built-in code, IF this device RAM-boots without modules",
     "one fastboot boot"),
    ("fast", "a CONFIG change, or anything that moves module CRCs -- builds and "
     "flashes the APORT release, so the change must be in the series",
     "flashes boot only"),
    ("kernel", "rootfs contents changed, or boot/rootfs desynced",
     "then `porthole flash --yes`"),
    ("upgrade", "the device moves to a DIFFERENT kernel flavor (a major version "
     "bump): PORTHOLE_KERNEL_PKG now names another aport, so kernel.release "
     "changes and the modules on the phone are absent rather than stale",
     "pushes modules, then flashes boot"),
]


def _script(ctx) -> pathlib.Path:
    path = pathlib.Path(ctx.root) / "tools" / "ph-build.sh"
    if not path.is_file():
        raise Bail(f"{path} is missing", EX_FAIL)
    return path


def _preflight(ctx) -> list[str]:
    """What must be true before a build can even start.

    Reported together rather than one failure at a time: an envkernel build is
    minutes long, and finding out about the second missing value after the
    first one is fixed is how an afternoon goes.
    """
    problems = []
    cfg = ctx.cfg
    for key in ("PORTHOLE_WORKDIR", "PORTHOLE_KERNEL_PKG", "PORTHOLE_DEFCONFIG",
                "PORTHOLE_ARCH", "PORTHOLE_DTB"):
        if not cfg.get(key):
            problems.append(f"{key} is not set in the profile")
    workdir = cfg.get("PORTHOLE_WORKDIR", "")
    if workdir and not pathlib.Path(workdir).is_dir():
        problems.append(f"PORTHOLE_WORKDIR does not exist: {workdir}")
    if not shutil.which("pmbootstrap"):
        problems.append("pmbootstrap is not on PATH")
    problems += _space_problems(cfg)
    return problems


# Measured on the reference host, 2026-08-29, on an established workdir:
#   chroot_native 15G · cache_apk_aarch64 2.0G · rootfs chroot 2.1G · total 29G
# A kernel tree and its objects sit on top of that. These are the headroom a
# build needs to FINISH, not the size of the result -- which is the number that
# matters, because running out at minute forty costs the whole build.
SPACE_FLOOR_GB = 5      # below this a build cannot finish; refuse
SPACE_WARN_GB = 20      # below this it may, and it is worth saying so


def _free_gb(path: str) -> float:
    st = os.statvfs(path)
    return (st.f_bsize * st.f_bavail) / (1024 ** 3)


def _space_problems(cfg) -> list[str]:
    """Refuse a build that cannot finish, in one second rather than forty
    minutes.

    pmbootstrap has its own check, but it runs after the chroots are prepared
    and only covers the image it is about to create. The expensive part is
    everything before that.
    """
    workdir = cfg.get("PORTHOLE_PMB_DIR") or str(
        pathlib.Path.home() / ".local/var/pmbootstrap")
    probe = pathlib.Path(workdir).expanduser()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = _free_gb(str(probe))
    except OSError:
        return []
    if free < SPACE_FLOOR_GB:
        return [f"only {free:.1f} GB free on {probe} -- a build needs at least "
                f"{SPACE_FLOOR_GB} GB to finish. Free some space, or point "
                f"PORTHOLE_PMB_DIR at a bigger filesystem"]
    return []


def _space_warning(cfg) -> str:
    """Not a refusal: tight but survivable, and worth knowing before you wait."""
    workdir = cfg.get("PORTHOLE_PMB_DIR") or str(
        pathlib.Path.home() / ".local/var/pmbootstrap")
    probe = pathlib.Path(workdir).expanduser()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = _free_gb(str(probe))
    except OSError:
        return ""
    if free < SPACE_WARN_GB:
        return (f"{free:.1f} GB free on {probe}. A full kernel rung wants "
                f"more like {SPACE_WARN_GB} GB; this may still work, but "
                f"ENOSPC at minute forty is the usual way it does not")
    return ""


def _assert_no_drift(ctx, args) -> None:
    """Refuse to build something other than what the profile says.

    A stale `export PORTHOLE_KERNEL_PKG=...-6.18` outranks a profile committed
    at 7.2, and nothing said so: the build succeeded and produced the retired
    kernel. Observed twice -- once poisoning a rootfs chroot. `porthole config`
    could always have shown it; nobody runs `porthole config` mid-build.
    """
    import porthole

    drifts = [d for d in porthole.drift(ctx.cfg) if d["blocking"]]
    advisories = [d for d in porthole.drift(ctx.cfg) if not d["blocking"]]
    for adv in advisories:
        ctx.out.warn(f"{adv['key']} is {adv['winning']} from the environment, "
                     f"but the {adv['committed_layer']} says "
                     f"{adv['committed']}")
    if not drifts or getattr(args, "allow_env_override", False):
        return

    detail = "\n  ".join(porthole.drift_lines(drifts))
    raise Bail(
        "the environment is overriding the profile:\n  " + detail,
        EX_FAIL,
        "the profile is committed; your shell is not.\n"
        "          unset " + " ".join(d["key"] for d in drifts)
        + "    use the profile\n"
        "          porthole build --allow-env-override     you mean it")


def _tree(cfg) -> pathlib.Path:
    """Where the kernel tree is, matching ph-build.sh:51 exactly."""
    tree = (cfg.get("PORTHOLE_KERNEL_TREE") or "").strip()
    if tree:
        return pathlib.Path(tree).expanduser()
    workdir = (cfg.get("PORTHOLE_WORKDIR") or "").strip()
    return pathlib.Path(workdir).expanduser() / "linux" if workdir else pathlib.Path()


def _classify(changed) -> tuple:
    """Which rung covers what an incremental make actually rebuilt.

    PURE -- a list of changed artifact paths in, (rung, args, reason) out --
    because this is the decision that must not be wrong, and a pure function is
    one that can be wrong in a test instead of on a device.

    Measured rather than inferred from the source diff, and the difference is
    not academic: a header edit moves every module's CRC without looking like a
    config change, and a Kconfig edit can flip a module to built-in. Both fool
    a diff reader. Neither fools "what did make actually write".
    """
    kos = sorted(p for p in changed if p.endswith(".ko"))
    image = [p for p in changed
             if p.endswith(("/Image.gz", "/Image", "Image.gz", "Image"))]
    dtbs = [p for p in changed if p.endswith(".dtb")]

    if not changed:
        return (None, [], "make rebuilt nothing -- there is nothing to push")

    if image:
        return ("fast", [],
                "Image.gz moved, so every module's CRC may have moved with it; "
                "a pushed module would be refused by the running kernel")

    if kos and not dtbs:
        if len(kos) == 1:
            name = pathlib.Path(kos[0]).name[:-len(".ko")]
            return ("mod", [kos[0], name],
                    f"one module rebuilt ({name}) and nothing else")
        # brain/traps/pushing-one-module-of-a-pair-corrupts-the-other.md: modules
        # built together share a struct layout, and pushing a subset corrupts
        # the ones left behind. So several is not several `mod` runs.
        return ("fast", [],
                f"{len(kos)} modules rebuilt together; pushing a subset "
                f"corrupts the ones left behind")

    if dtbs and not kos:
        return ("boot", [], "only the dtb changed")

    return ("fast", [],
            "both the dtb and modules changed, and no cheap rung covers both")


def _changed_artifacts(tree: pathlib.Path, since) -> list:
    """Artifacts under .output newer than `since`, as tree-relative paths."""
    out = tree / ".output"
    if not out.is_dir():
        return []
    found = []
    for path in out.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".ko", ".dtb") and path.name not in (
                "Image.gz", "Image"):
            continue
        try:
            if path.stat().st_mtime > since:
                found.append(str(path.relative_to(out)))
        except OSError:
            continue
    return sorted(found)


def _workspace_usable(ctx):
    """(usable, why_not) -- is the workspace wired for THIS device?

    A merely RUNNING container is not enough, and assuming it was cost a real
    session three confused build attempts: the container had been created for a
    different device, so the build routed into it and died with "could not read
    pkgver/pkgrel / aport is ." -- an error with no relationship to the actual
    problem. The only clue was a grey line saying "in the workspace".

    So the container has to agree with us about which phone this is. It already
    records that as a label at creation time, for the device-mutex guard; this
    reads the same label. Never raises: deciding WHERE to build must not be a
    way for the build verb to break.
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
    except Exception:  # noqa: BLE001
        return False, "could not query the workspace"
    return True, ""


def _tree_inside(tree, workdir) -> str:
    """PORTHOLE_KERNEL_TREE as the CONTAINER sees it, or "" if it cannot.

    A worktree is the toolbox's own advice -- ph-build prints "set
    PORTHOLE_KERNEL_TREE to build a worktree" on every run -- and the knob is a
    host path, so it stops at the container boundary like every other one
    (docs/SANDBOX-PROVISIONING.md §4b). The difference is that this one CAN be
    translated: a worktree kept inside the device repo is mounted, at /work.

    A tree outside the device repo is not reachable inside at all, so this
    returns "" and the caller leaves the variable unset -- the container then
    builds its default tree, which is wrong but visibly so, rather than dying
    on a path that does not exist.
    """
    if not tree or not workdir:
        return ""
    try:
        rel = pathlib.Path(tree).expanduser().resolve().relative_to(
            pathlib.Path(workdir).expanduser().resolve())
    except (ValueError, OSError):
        return ""
    return "/work" if str(rel) == "." else f"/work/{rel}"


def _container_cmd(func: str, extra: list[str] | None,
                   secrets, tree_inside: str = "") -> list[str]:
    """The podman exec line for a build, as argv.

    Pure, so where a build runs is testable without podman, a device or a
    workdir -- none of which CI has.

    The container's own PORTHOLE_* values were set when it was created and are
    correct for the paths INSIDE it, so they are deliberately not re-sent from
    the host, where PORTHOLE_WORKDIR names a directory that does not exist in
    here. Only TK_* runtime values cross, because those are things the user set
    for this invocation -- a password among them.
    """
    import porthole_cmd_sandbox as sandbox

    call = " ".join([func, *(shlex.quote(a) for a in extra or [])])
    argv = ["podman", "exec"]
    # TK_ only, enforced HERE rather than trusting the caller to filter: the
    # rule is that host paths never cross, and a rule that lives in the caller
    # is one a second caller will not have. PORTHOLE_WORKDIR is the one that
    # bites -- the host's names a directory that does not exist inside, and
    # sending it is what made a real session refuse to build.
    #
    # `-e NAME`, not `-e NAME=value`: podman takes the value from OUR
    # environment, so a rootfs password never appears in the podman argv where
    # `ps` would show it to every user on the box. TK_PMOS_PASSWORD is exactly
    # such a value, and tkbuild requires it.
    for key in sorted(k for k in secrets if k.startswith("TK_")):
        argv += ["-e", key]
    # The one host path that is translated rather than dropped: see
    # _tree_inside. Sent as NAME=value, not NAME -- the value is the
    # container's path, not ours, so it cannot come from our environment.
    if tree_inside:
        argv += ["-e", f"PORTHOLE_KERNEL_TREE={tree_inside}"]
    argv += [sandbox.CONTAINER, "/bin/bash", "-lc",
             f"cd /porthole && source tools/ph-build.sh && {call}"]
    return argv


def _host_cmd(script: pathlib.Path, func: str,
              extra: list[str] | None) -> list[str]:
    """bash, not sh: ph-build.sh uses arrays, `shopt -s expand_aliases` and
    `pushd`, and envkernel's `make` is an alias only bash expands."""
    call = " ".join([func, *(shlex.quote(a) for a in extra or [])])
    return ["bash", "-c", f'source "{script}" && {call}']


def _run(ctx, func: str, timeout: int, extra: list[str] | None = None,
         host: bool = False, rung: str = "") -> int:
    """Run one of ph-build.sh's functions, in the workspace or on the host."""
    script = _script(ctx)
    env = dict(os.environ)
    for key, value in ctx.cfg.items():
        if key.startswith(("PORTHOLE_", "TK_")) and isinstance(value, str):
            env[key] = value

    # shlex.quote, not naive interpolation: these arguments are a path and a
    # module name that reach a shell, and a path with a space in it would
    # otherwise arrive as two arguments.
    call = " ".join([func, *(shlex.quote(a) for a in extra or [])])
    usable, why_not = (False, "--host") if host else _workspace_usable(ctx)
    if usable:
        secrets = {k: v for k, v in env.items()
                   if k.startswith("TK_") and isinstance(v, str)}
        cmd = _container_cmd(func, extra, secrets,
                             _tree_inside(env.get("PORTHOLE_KERNEL_TREE"),
                                          env.get("PORTHOLE_WORKDIR")))
        # Not grey. WHERE a build ran is the first thing you need when it fails
        # in a way that makes no sense, and burying it cost someone three
        # attempts before they noticed the tail.
        ctx.out(ctx.out.paint("  building IN THE WORKSPACE (container)", "cyan"))
    else:
        cmd = _host_cmd(script, func, extra)
        ctx.out(ctx.out.paint(f"  building ON THE HOST ({why_not})", "cyan"))
    return _stream(ctx, cmd, env, timeout, rung or func)


def _stream(ctx, cmd, env, timeout: int, rung: str) -> int:
    """Run the build, publishing where it is the whole time.

    Every line goes to a log file unconditionally, so "quiet by default" never
    costs anyone the output they needed. The terminal gets a live bar when it
    is a terminal, a periodic line when it is not (an agent's pipe), and the
    raw stream under --verbose.
    """
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    rundir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logpath = rundir / f"build-{rung}-{stamp}.log"
    verbose = getattr(getattr(ctx, "args", None), "verbose", False)
    tty = sys.stdout.isatty()

    tracker = progress.Tracker(rundir, rung)
    tracker.publish(force=True)
    ctx.out(ctx.out.paint(f"  log: {logpath}", "grey"))

    try:
        proc = subprocess.Popen(cmd, env=env, cwd=str(ctx.root),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
    except FileNotFoundError:
        raise Bail("bash is not installed", EX_FAIL) from None

    last_note = 0.0
    killed = False
    try:
        with open(logpath, "w") as log:
            for line in proc.stdout:
                log.write(line)
                tracker.feed(line)
                tracker.publish()
                if verbose:
                    sys.stdout.write(line)
                elif tty:
                    sys.stdout.write("\r\033[2K  " + tracker.line())
                    sys.stdout.flush()
                elif time.time() - last_note > 15:
                    # Not a terminal: an agent's pipe, or CI. A repainting bar
                    # would be thousands of useless lines, and silence is the
                    # black box this exists to end. One line every 15s is both
                    # readable and enough to see it is alive.
                    last_note = time.time()
                    print("  " + tracker.line(), flush=True)
                if tracker.elapsed > timeout:
                    killed = True
                    proc.kill()
                    break
    finally:
        if tty and not verbose:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
        rc = proc.wait()
        tracker.finish(rc == 0 and not killed)

    if killed:
        raise Bail(f"{rung} timed out after {timeout}s", EX_FAIL,
                   f"the partial log is at {logpath}")
    ctx.out(ctx.out.paint(
        f"  {rung}: {progress.fmt_dur(tracker.elapsed)}"
        f"  ({tracker.compile_seen} compile steps)", "grey"))
    # A failed build otherwise says only "0 compile steps" and exits 1: the
    # reason is in the log, and whoever is reading this -- an agent especially
    # -- has no idea a log is worth opening. The last few lines are where the
    # shell says why it refused, every time.
    if rc != 0 and not verbose:
        tail = [ln.rstrip() for ln in logpath.read_text(
            errors="replace").splitlines() if ln.strip()][-6:]
        for line in tail:
            ctx.out(ctx.out.paint(f"  | {line}", "grey"))
    return rc


def _rung_args(args, action: str) -> list[str]:
    """Validate and shape the trailing arguments a rung takes.

    Checked here rather than in the shell because `tkmod` with one argument
    builds every module in the tree and then fails on a path it cannot resolve
    -- minutes spent to learn about a typo.
    """
    rest = list(getattr(args, "rest", None) or [])
    if action == "mod":
        if len(rest) != 2:
            raise Bail("mod needs the module's path and its name", EX_USAGE,
                       "porthole build mod drivers/media/i2c/imx179.ko imx179 --yes")
        return rest
    if rest:
        raise Bail(f"{action} takes no extra arguments", EX_USAGE,
                   "only `mod` takes arguments (MODULE.ko NAME)")
    if action == "boot" and getattr(args, "kernel", False):
        return ["--kernel"]
    if getattr(args, "kernel", False):
        raise Bail(f"--kernel applies to `boot`, not `{action}`", EX_USAGE)
    return []


def _auto(ctx, args) -> int:
    """Make first, then run the cheapest rung that covers what changed.

    The old default was `kernel` -- the MOST expensive of five rungs, at ~10
    minutes against `mod`'s ~40 seconds. An agent typing the obvious
    `porthole build --yes` got the 15x path, which is most of why sessions were
    spending ten minutes on changes a module push covered.

    Note this compiles even without --yes. That is a deliberate departure from
    the other rungs' preview: the whole question `auto` answers is "which rung",
    and only make can answer it. It touches NO device without --yes.
    """
    import time

    tree = _tree(ctx.cfg)
    if not (tree / "Makefile").is_file():
        raise Bail(f"no kernel tree at {tree}", EX_FAIL,
                   "set PORTHOLE_KERNEL_TREE, or PORTHOLE_WORKDIR with linux/ "
                   "inside it")

    # A second early, because make writes files as it runs and a clock that
    # ticks between the stamp and the first write would hide the first object.
    since = time.time() - 1
    ctx.out(ctx.out.paint("  measuring: incremental make, then routing on what "
                          "it actually rebuilt", "grey"))
    # _ph_measure, not _ph_make: the only question here is what make touched,
    # and _changed_artifacts below answers it out of .output. _ph_make would
    # also package -- 14.66 s, and a `_p` apk left in the local repo that
    # outranks every release build. A preview must not do that.
    rc = _run(ctx, "_ph_measure", args.timeout, None,
              host=getattr(args, "host", False), rung="auto")
    if rc != 0:
        return rc

    changed = _changed_artifacts(tree, since)
    rung, extra, reason = _classify(changed)

    ctx.out.blank()
    ctx.out.heading("what make rebuilt")
    for path in changed[:8]:
        ctx.out(f"  {path}")
    if len(changed) > 8:
        ctx.out(f"  ... and {len(changed) - 8} more")
    if not changed:
        ctx.out(ctx.out.paint("  nothing", "grey"))
    ctx.out.blank()

    if rung is None:
        ctx.out(reason)
        return EX_OK

    func, what = ACTIONS[rung]
    call = " ".join(["porthole", "build", rung, *extra, "--yes"])
    ctx.out.heading(f"cheapest rung that covers it: {rung}")
    ctx.out(f"  {reason}")
    ctx.out(f"  {what}")
    ctx.out(ctx.out.paint(f"  {call}", "cyan"))
    ctx.out.blank()

    if not args.yes:
        ctx.out(ctx.out.paint(
            "  nothing was pushed or flashed -- add --yes to run that rung",
            "grey"))
        return EX_OK
    return _run(ctx, func, args.timeout, extra,
                host=getattr(args, "host", False), rung=rung)


def _status(ctx) -> int:
    """Where the running build is. This is what an agent polls INSTEAD of
    sleeping -- brain/laws/poll-never-sleep.md said not to sleep, and until
    now could not say what to read."""
    import porthole_progress as progress

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    path = rundir / "build-status.json"
    try:
        snap = json.loads(path.read_text())
    except (OSError, ValueError):
        return ctx.emit({"state": "none"},
                        lambda: ctx.out("no build has run in this checkout"))

    def render():
        ctx.out(f"  {progress.bar(snap.get('progress'))} "
                f"{snap.get('phase', '?')}")
        ctx.out.kv("rung", snap.get("rung", "?"), 10)
        ctx.out.kv("state", snap.get("state", "?"), 10)
        ctx.out.kv("elapsed", progress.fmt_dur(snap.get("elapsed")), 10)
        ctx.out.kv("eta", progress.fmt_dur(snap.get("eta")), 10)
        if snap.get("last"):
            ctx.out.kv("last", snap["last"][:100], 10)

    return ctx.emit(snap, render)


def cmd_build(args, ctx) -> int:
    action = args.action or "auto"
    # `status` and `auto` are actions, not flags. A store_true `--status` would
    # be a MODE encoded as a boolean, which permits nonsense combinations and
    # is what tests/test_cli_rules.py forbids repo-wide.
    if action == "status":
        return _status(ctx)
    if action != "auto" and action not in ACTIONS:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: auto, status, {', '.join(ACTIONS)}")

    _assert_no_drift(ctx, args)

    if action == "auto":
        # Measuring needs a tree to make in. Without one -- a new port, or a
        # profile nobody has filled in yet -- fall through to the ordinary
        # preview rather than erroring: "a profile that cannot build yet is the
        # normal state of a new port", and a preview SHOWS what is wrong
        # instead of refusing to describe it.
        if not _preflight(ctx) and (_tree(ctx.cfg) / "Makefile").is_file():
            return _auto(ctx, args)
        func, what = "", AUTO_DESC
        extra = []
    else:
        func, what = ACTIONS[action]
        extra = _rung_args(args, action)

    problems = _preflight(ctx)
    tight = _space_warning(ctx.cfg)
    if tight and not problems:
        ctx.out.warn(tight)

    # A preview SHOWS what is wrong; it does not refuse. Refusing to describe
    # the build because the build could not run is unhelpful precisely when you
    # most need to know why -- and a profile that cannot build yet is the
    # normal state of a new port.
    if not args.yes and action in BUILD_ACTIONS:
        def render():
            o = ctx.out
            o.heading(f"would {what}")
            o.kv("device", ctx.cfg.get("PORTHOLE_DEVICE", ""), 12)
            o.kv("tree", ctx.cfg.get("PORTHOLE_WORKDIR", "")
                 or o.paint("not set", "yellow"), 12)
            o.kv("package", ctx.cfg.get("PORTHOLE_KERNEL_PKG", "")
                 or o.paint("not set", "yellow"), 12)
            o.kv("defconfig", ctx.cfg.get("PORTHOLE_DEFCONFIG", "")
                 or o.paint("not set", "yellow"), 12)
            o.blank()
            if problems:
                o.heading(f"{len(problems)} thing(s) missing first")
                for problem in problems:
                    o(f"  {o.paint(o.sym('·', '-'), 'yellow')} {problem}")
                o.blank()
            # The ladder, every time. The expensive mistake here is not a bad
            # build, it is iterating on the ~10 minute rung when the ~40 second
            # one covers the change -- and nothing used to say the cheap rungs
            # existed.
            o.heading("pick the cheapest rung that covers your change")
            for name, covers, cost in LADDER:
                mark = o.sym(">", "*") if name == action else " "
                o(f"  {mark} {o.paint(name.ljust(7), 'cyan')} {covers}"
                  f"  {o.paint('(' + cost + ')', 'grey')}")
            o.blank()
            if not problems:
                o.hint(f"porthole build {' '.join([action, *extra])} --yes")
        return ctx.emit({"action": action, "function": func, "args": extra,
                         "would_run": not problems, "problems": problems,
                         "ladder": [dict(zip(("rung", "covers", "cost"), r))
                                    for r in LADDER]},
                        render)

    if problems and action in BUILD_ACTIONS:
        raise Bail("this profile cannot build yet", EX_FAIL,
                   "; ".join(problems))

    if not func:
        # `auto` with --yes but nothing to measure with. Say which of the two
        # it is rather than running an empty command.
        raise Bail("cannot choose a rung: there is no kernel tree to measure",
                   EX_FAIL,
                   f"expected {_tree(ctx.cfg)}/Makefile -- set "
                   f"PORTHOLE_KERNEL_TREE, or name an explicit rung")

    rc = _run(ctx, func, args.timeout, extra,
              host=getattr(args, "host", False), rung=action)
    if rc != 0:
        raise Bail(f"{func} failed", EX_FAIL,
                   "the output above is the build's; `pmbootstrap log` has more")
    ctx.out(ctx.out.paint(f"  {what}: done", "green"))
    return EX_OK


SPEC = {
    "verb": "build",
    "order": 36,
    "help": "build the kernel and package it, through envkernel",
    "description": (
        "The envkernel loop, as a verb. It was only ever a shell file you had\n"
        "to SOURCE, which meant `porthole run` could not reach it and the\n"
        "second half of a port had no command at all.\n\n"
        "Two traps are encoded in the script it drives, both paid for in real\n"
        "sessions: `pmbootstrap build --envkernel` can write an apk and then\n"
        "fail before refreshing the index, and a stale _p snapshot outranks a\n"
        "release build. Artifacts are verified rather than exit codes trusted."),
    "escapes_scope": True,
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "choices": ["auto", "status"] + list(ACTIONS),
                      "help": "auto | status | " + " | ".join(ACTIONS) + "  (default: auto)"}),
        (["rest"], {"nargs": "*", "metavar": "ARG",
                    "help": "mod: MODULE.ko NAME"}),
        (["--kernel"], {"action": "store_true",
                        "help": "boot: rebuild Image.gz too, not just dtbs"}),
        (["--timeout"], {"type": int, "default": 5400, "metavar": "SEC",
                         "help": "seconds before giving up (default 5400)"}),
        (["--allow-env-override"], {"action": "store_true",
                                    "help": "build what the environment says, "
                                            "not what the profile says"}),
        (["--verbose"], {"action": "store_true",
                         "help": "stream the raw build output instead of a "
                                 "progress line"}),
        (["--host"], {"action": "store_true",
                      "help": "build on the host, not in the workspace"}),
        (["--yes"], {"action": "store_true", "help": "actually build"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_build,
    "examples": [
        "porthole build",
        "porthole build mod drivers/media/i2c/imx179.ko imx179 --yes",
        "porthole build boot --yes",
        "porthole build boot --kernel --yes",
        "porthole build fast --yes",
        "porthole build kernel --yes",
        "porthole build clean",
    ],
}
