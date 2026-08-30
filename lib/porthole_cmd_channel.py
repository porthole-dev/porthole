# SPDX-License-Identifier: MIT
"""`porthole channel` -- see and switch the postmarketOS release channel.

Switching a channel changes which pmaports branch and which Alpine mirror
everything is built against. pmbootstrap handles the mechanics; what it does
not do is tell you, before you commit, what a switch is going to cost you --
which is the part people actually want to know.
"""
from __future__ import annotations

import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK
import porthole_pmaports as pmap


def channel_of_branch(branch: str) -> str:
    """The channel a pmaports branch represents.

    pmbootstrap 3.x dropped `channel` as a config key -- asking for it makes
    argparse reject the name, and porthole rendered that error as
    `current: unknown` for months. The channel was never really a setting: it
    is which branch pmaports is on, and `master` is spelled `edge` everywhere
    a human reads it.
    """
    if not branch:
        return ""
    return "edge" if branch == "master" else branch


def current(cfg) -> str:
    """Which channel this checkout is on, read from pmaports itself.

    No pmbootstrap call: the answer is in the checkout, and asking a tool
    that no longer knows produced a confident "unknown".
    """
    pmaports = pmap.find_pmaports(cfg)
    if not pmaports:
        return ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(pmaports), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return channel_of_branch(proc.stdout.strip()) if proc.returncode == 0 else ""


def cmd_channel(args, ctx) -> int:
    pmaports = pmap.find_pmaports(ctx.cfg)
    if not pmaports:
        raise Bail("no pmaports checkout found", EX_FAIL,
                   "run `pmbootstrap init` once so it clones one")
    chans = pmap.channels(pmaports)
    meta = chans.pop("_meta", {})
    now = current(ctx.cfg)

    if not args.name:
        payload = {"current": now, "recommended": meta.get("recommended", ""),
                   "channels": [{"name": k, **v} for k, v in chans.items()]}

        def render():
            o = ctx.out
            o.heading("release channels")
            o.blank()
            width = max((len(k) for k in chans), default=8)
            for name, info in chans.items():
                mark = o.paint(o.sym("●", "*"), "green") if name == now else " "
                tag = ""
                if name == meta.get("recommended"):
                    tag = o.paint("  (recommended)", "cyan")
                desc = info.get("description", "").split(":")[0]
                o(f" {mark} {name:<{width}}  {desc}{tag}")
            o.blank()
            o(f"  current: {o.paint(now or 'unknown', 'bold')}")
            o.blank()
            o.hint("porthole channel <name>   switch")
        return ctx.emit(payload, render)

    if args.name not in chans:
        raise Bail(f"no channel {args.name!r}", EX_FAIL,
                   f"available: {', '.join(chans)}")
    if args.name == now:
        ctx.out(f"already on {now}.")
        return EX_OK

    info = chans[args.name]
    ctx.out.heading(f"switch {now or '?'} -> {args.name}")
    ctx.out.blank()
    for key in ("description", "branch_pmaports", "branch_aports",
                "mirrordir_alpine"):
        if info.get(key):
            ctx.out.kv(key, info[key], 18)
    ctx.out.blank()
    ctx.out(ctx.out.paint(
        "  This switches the pmaports branch and the Alpine mirror. Expect a\n"
        "  full rebuild: your chroots are built against the old channel and\n"
        "  pmbootstrap will zap what no longer matches.", "yellow"))
    ctx.out.blank()
    if ctx.cfg.get("PORTHOLE_PMAPORTS_DIRTY_WARN") != "0":
        dirty = _dirty(pmaports)
        if dirty:
            ctx.out(ctx.out.paint(
                f"  WARNING: {len(dirty)} uncommitted change(s) in pmaports.\n"
                f"  Switching branches will carry or clobber them. Commit or\n"
                f"  stash first -- `porthole aports status`.", "red"))
            ctx.out.blank()

    if not args.yes:
        ctx.out.hint(f"porthole channel {args.name} --yes    to go ahead")
        return EX_OK

    rc = subprocess.run(["pmbootstrap", "config", "channel", args.name]).returncode
    if rc != 0:
        raise Bail("pmbootstrap refused the channel change", EX_FAIL)
    ctx.out(ctx.out.paint(f"  channel is now {args.name}", "green"))
    ctx.out.hint("pmbootstrap pull     # sync pmaports to the new branch")
    return EX_OK


def _dirty(pmaports) -> list[str]:
    try:
        proc = subprocess.run(["git", "-C", str(pmaports), "status", "--porcelain"],
                              capture_output=True, text=True, timeout=20)
        return [l for l in proc.stdout.splitlines() if l.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


SPEC = {
    "verb": "channel",
    "order": 44,
    "help": "see and switch the postmarketOS release channel",
    "description": (
        "A channel selects a pmaports branch and an Alpine mirror. Switching\n"
        "means a rebuild, so this shows what the switch costs before you make\n"
        "it, and warns if pmaports has uncommitted work that a branch change\n"
        "would carry or clobber."),
    "args": [
        (["name"], {"nargs": "?", "help": "channel to switch to"}),
        (["--yes"], {"action": "store_true", "help": "actually switch"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_channel,
    "examples": ["porthole channel", "porthole channel v26.06 --yes"],
}
