#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole use` -- switch the active device, and everything that follows it.

Working two ports at once means switching more than a codename. The device has
a working repo (its kernel tree, its notes), pmaports may be on that device's
topic branch, and every tool resolves its host and port from config. Doing that
by hand means editing `~/.config/porthole/config.env` and remembering which
directory went with which phone -- and the failure mode is silent: you flash
taimen's boot image at a Pixel 7 because PORTHOLE_DEVICE still said taimen.

So `use` is one command that moves all of it, and `porthole cd` prints the
directory so a shell can follow:

    porthole use google-cheetah
    cd "$(porthole cd)"

`cd` prints a bare path and nothing else, because its whole job is to be
substituted into a shell command. That is also why it takes a target: the
device repo, the kernel tree and pmaports are three different places you need
to get to, and `cd $(porthole cd pmaports)` beats remembering where pmbootstrap
cloned it.
"""
from __future__ import annotations

import os
import pathlib
import re

import porthole
import porthole_pmaports as pmap
from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE


def config_path() -> pathlib.Path:
    xdg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME")
                       or pathlib.Path.home() / ".config")
    return xdg / "porthole" / "config.env"


def set_key(path: pathlib.Path, key: str, value: str) -> None:
    """Rewrite one KEY= line in place, preserving everything else.

    In place rather than rewritten from a template: this file is the user's,
    it carries their comments and their hand-added keys, and a switch command
    that quietly drops those would be a worse bug than the one it fixes.
    """
    line = f"{key}={value}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(line + "\n")
        return
    text = path.read_text()
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    path.write_text(pattern.sub(line, text) if pattern.search(text)
                    else text.rstrip("\n") + "\n" + line + "\n")


# --------------------------------------------------------------------- use --

def cmd_use(args, ctx) -> int:
    root = pathlib.Path(ctx.root)
    profiles = porthole.list_profiles(root)

    if not args.codename:
        # No argument is a question, not an error: show what is selectable.
        current = ""
        try:
            current = porthole.load_config(root=root).get("PORTHOLE_DEVICE", "")
        except porthole.ProfileNotFound:
            pass
        payload = {"active": current, "profiles": profiles}

        def render():
            o = ctx.out
            o.heading("device profiles")
            for name in profiles:
                mark = o.paint(o.sym("●", "*"), "green") if name == current else " "
                o(f"  {mark} {name}")
            if not profiles:
                o(o.paint("  none yet", "grey"))
            o.blank()
            o.hint("porthole use <codename>       switch to one")
            o.hint("porthole new-device <name>    create one")

        return ctx.emit(payload, render)

    codename = args.codename
    if codename not in profiles:
        import difflib
        match = difflib.get_close_matches(codename, profiles, n=1, cutoff=0.6)
        raise Bail(
            f"no profile for {codename!r}", EX_FAIL,
            (f"did you mean {match[0]!r}?" if match
             else f"porthole new-device {codename}   to create one"))

    target = config_path()
    set_key(target, "PORTHOLE_DEVICE", codename)
    per_device = f"PORTHOLE_WORKDIR_{codename.upper().replace('-', '_')}"

    # A workdir declared by the profile follows the device. Declared in the
    # PROFILE, not guessed from a sibling directory: guessing which folder goes
    # with which phone is the mistake this command exists to stop making.
    cfg = porthole.load_config(root=root, env={**os.environ,
                                              "PORTHOLE_DEVICE": codename})
    if args.workdir:
        workdir = str(pathlib.Path(args.workdir).expanduser().resolve())
        set_key(target, per_device, workdir)
        cfg.set("PORTHOLE_WORKDIR", workdir, "user")
    workdir = cfg.get(per_device) or ""

    warnings = []
    if workdir and not pathlib.Path(workdir).is_dir():
        warnings.append(f"workdir does not exist: {workdir}")
    elif not workdir:
        # Report the absence. Inheriting the previous device's repo is exactly
        # the bug this key exists to remove, so silence here would be a
        # regression wearing a different name.
        stale = cfg.get("PORTHOLE_WORKDIR", "")
        warnings.append(
            f"{codename} has no working repo set"
            + (f" (the global PORTHOLE_WORKDIR points at {stale}, which "
               f"belongs to whichever device set it — it is NOT being used "
               f"for {codename})" if stale else ""))

    branch = ""
    pmaports = pmap.find_pmaports(cfg)
    if pmaports:
        import subprocess
        try:
            branch = subprocess.run(
                ["git", "-C", str(pmaports), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            branch = ""

    payload = {"device": codename, "config": str(target), "workdir": workdir,
               "pmaports": str(pmaports) if pmaports else "",
               "pmaports_branch": branch, "warnings": warnings}

    def render():
        o = ctx.out
        o(f"{o.paint(o.sym('✓', 'ok'), 'green')} now on {o.paint(codename, 'bold')}")
        o.kv("workdir", workdir or o.paint("not set", "yellow"), 10)
        if pmaports:
            o.kv("pmaports", f"{pmaports}  ({branch or 'unknown branch'})", 10)
        for w in warnings:
            o.warn(w)
        o.blank()
        # pmaports is SHARED between devices and does not follow this switch.
        # Saying so is the point: a branch left on another device's topic is
        # exactly how a change lands in the wrong package.
        if branch and branch not in ("master", "main"):
            o.hint(f"pmaports is on {branch!r} — shared across devices, and it "
                   f"did NOT switch")
            o.hint("porthole aports status    check before you build")
        if not workdir:
            o.hint(f"porthole use {codename} --workdir <path>   set its repo")
        o.hint('cd "$(porthole cd)"       go to the device repo')

    return ctx.emit(payload, render)


# ---------------------------------------------------------------------- cd --

TARGETS = ("workdir", "kernel", "pmaports", "profile", "porthole")


def cmd_cd(args, ctx) -> int:
    """Print ONE bare path on stdout. No decoration, ever -- it is substituted."""
    what = args.target or "workdir"
    if what not in TARGETS:
        raise Bail(f"unknown target {what!r}", EX_USAGE,
                   f"targets: {', '.join(TARGETS)}")
    cfg = ctx.cfg
    root = pathlib.Path(ctx.root)

    if what == "porthole":
        path = root
    elif what == "profile":
        path = root / "profiles" / cfg.get("PORTHOLE_DEVICE", "")
    elif what == "pmaports":
        found = pmap.find_pmaports(cfg)
        if not found:
            raise Bail("no pmaports checkout found", EX_FAIL,
                       "run `pmbootstrap init` once, or set PORTHOLE_PMAPORTS")
        path = found
    elif what == "kernel":
        workdir = cfg.get("PORTHOLE_WORKDIR", "")
        if not workdir:
            raise Bail("PORTHOLE_WORKDIR is not set", EX_FAIL,
                       "porthole use <codename> --workdir <path>")
        path = pathlib.Path(workdir) / "linux"
    else:
        workdir = cfg.get("PORTHOLE_WORKDIR", "")
        if not workdir:
            raise Bail("PORTHOLE_WORKDIR is not set", EX_FAIL,
                       "porthole use <codename> --workdir <path>")
        path = pathlib.Path(workdir)

    if not path.exists():
        raise Bail(f"{path} does not exist", EX_FAIL,
                   "porthole use <codename> --workdir <path>   to correct it")
    print(path)
    return EX_OK


SPEC = {
    "verb": "use",
    "order": 12,
    "help": "switch the active device profile, and its working repo",
    "description": (
        "Rewrites PORTHOLE_DEVICE in your config.env, so every tool, every\n"
        "shell and every later session agrees which phone you mean.\n\n"
        "A profile that declares PORTHOLE_WORKDIR brings its repo along.\n"
        "pmaports deliberately does NOT follow: it is one shared checkout, and\n"
        "moving its branch behind your back is how a change lands in the wrong\n"
        "package. `use` reports the branch instead."),
    "device_flag": False,
    "args": [
        (["codename"], {"nargs": "?", "help": "profile to switch to"}),
        (["--workdir"], {"metavar": "PATH",
                         "help": "set this device's working repo while switching"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_use,
    "examples": [
        "porthole use",
        "porthole use google-cheetah",
        "porthole use google-cheetah --workdir ~/src/cheetah",
    ],
}
