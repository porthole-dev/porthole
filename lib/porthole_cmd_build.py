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

import os
import pathlib
import shlex
import shutil
import subprocess

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
BUILD_ACTIONS = ("mod", "boot", "fast", "kernel", "upgrade")

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
    return problems


def _run(ctx, func: str, timeout: int, extra: list[str] | None = None) -> int:
    """Source ph-build.sh and call one of its functions.

    bash, not sh: it uses arrays, `shopt -s expand_aliases` and `pushd`, and
    envkernel's `make` is an alias that only bash will expand.
    """
    script = _script(ctx)
    env = dict(os.environ)
    for key, value in ctx.cfg.items():
        if key.startswith(("PORTHOLE_", "TK_")) and isinstance(value, str):
            env[key] = value
    # shlex.quote, not naive interpolation: these arguments are a path and a
    # module name that reach a shell, and a path with a space in it would
    # otherwise arrive as two arguments.
    call = " ".join([func, *(shlex.quote(a) for a in extra or [])])
    cmd = ["bash", "-c", f'source "{script}" && {call}']
    ctx.out(ctx.out.paint(f"  $ source ph-build.sh && {call}", "grey"))
    try:
        return subprocess.run(cmd, env=env, cwd=str(ctx.root),
                              timeout=timeout).returncode
    except FileNotFoundError:
        raise Bail("bash is not installed", EX_FAIL) from None
    except subprocess.TimeoutExpired:
        raise Bail(f"{func} timed out after {timeout}s", EX_FAIL) from None


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


def cmd_build(args, ctx) -> int:
    action = args.action or "kernel"
    if action not in ACTIONS:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: {', '.join(ACTIONS)}")
    func, what = ACTIONS[action]
    extra = _rung_args(args, action)

    problems = _preflight(ctx)

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

    rc = _run(ctx, func, args.timeout, extra)
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
        (["action"], {"nargs": "?", "metavar": "ACTION", "choices": list(ACTIONS),
                      "help": " | ".join(ACTIONS) + "  (default kernel)"}),
        (["rest"], {"nargs": "*", "metavar": "ARG",
                    "help": "mod: MODULE.ko NAME"}),
        (["--kernel"], {"action": "store_true",
                        "help": "boot: rebuild Image.gz too, not just dtbs"}),
        (["--timeout"], {"type": int, "default": 5400, "metavar": "SEC",
                         "help": "seconds before giving up (default 5400)"}),
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
