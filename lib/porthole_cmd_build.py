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
import shutil
import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE

# Each verb maps to a shell function ph-build.sh defines. The names are kept
# from the taimen toolbox because they are what every runbook prints and what
# people already type interactively.
ACTIONS = {
    "kernel": ("tkbuild", "build the kernel, package it, install and verify"),
    "fast": ("tkfast", "kernel only, UUIDs untouched -- the iteration loop"),
    "clean": ("tkclean", "unstack /mnt/linux binds"),
    "purge": ("tkpurge-devpkgs", "remove envkernel apks that outrank a release"),
}


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


def _run(ctx, func: str, timeout: int) -> int:
    """Source ph-build.sh and call one of its functions.

    bash, not sh: it uses arrays, `shopt -s expand_aliases` and `pushd`, and
    envkernel's `make` is an alias that only bash will expand.
    """
    script = _script(ctx)
    env = dict(os.environ)
    for key, value in ctx.cfg.items():
        if key.startswith(("PORTHOLE_", "TK_")) and isinstance(value, str):
            env[key] = value
    cmd = ["bash", "-c", f'source "{script}" && {func}']
    ctx.out(ctx.out.paint(f"  $ source ph-build.sh && {func}", "grey"))
    try:
        return subprocess.run(cmd, env=env, cwd=str(ctx.root),
                              timeout=timeout).returncode
    except FileNotFoundError:
        raise Bail("bash is not installed", EX_FAIL) from None
    except subprocess.TimeoutExpired:
        raise Bail(f"{func} timed out after {timeout}s", EX_FAIL) from None


def cmd_build(args, ctx) -> int:
    action = args.action or "kernel"
    if action not in ACTIONS:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: {', '.join(ACTIONS)}")
    func, what = ACTIONS[action]

    problems = _preflight(ctx)
    if problems and action in ("kernel", "fast"):
        raise Bail("this profile cannot build yet", EX_FAIL,
                   "; ".join(problems))

    if not args.yes and action in ("kernel", "fast"):
        def render():
            o = ctx.out
            o.heading(f"would {what}")
            o.kv("device", ctx.cfg.get("PORTHOLE_DEVICE", ""), 12)
            o.kv("tree", ctx.cfg.get("PORTHOLE_WORKDIR", ""), 12)
            o.kv("package", ctx.cfg.get("PORTHOLE_KERNEL_PKG", ""), 12)
            o.kv("defconfig", ctx.cfg.get("PORTHOLE_DEFCONFIG", ""), 12)
            o.blank()
            o("  This compiles a kernel. It takes minutes and it writes into")
            o("  your pmbootstrap chroot.")
            o.blank()
            o.hint(f"porthole build {action} --yes")
        return ctx.emit({"action": action, "function": func,
                         "would_run": True}, render)

    rc = _run(ctx, func, args.timeout)
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
        (["--timeout"], {"type": int, "default": 5400, "metavar": "SEC",
                         "help": "seconds before giving up (default 5400)"}),
        (["--yes"], {"action": "store_true", "help": "actually build"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_build,
    "examples": [
        "porthole build",
        "porthole build kernel --yes",
        "porthole build fast --yes",
        "porthole build clean",
    ],
}
