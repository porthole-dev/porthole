# SPDX-License-Identifier: MIT
"""`porthole ui` -- see and switch the compositor / desktop.

phosh, plasma-mobile, gnome-mobile, sway, niri... pmOS ships 20-odd. Switching
is a one-liner to pmbootstrap, but knowing which exist and which suit a phone
means reading APKBUILDs, so this reads them for you.
"""
from __future__ import annotations

import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK
import porthole_pmaports as pmap

# UIs designed for a touchscreen phone. The rest are desktop or appliance
# shells that will run but were not built for a handset, and someone porting a
# phone wants that distinction without reading twenty APKBUILDs.
MOBILE = {"phosh", "plasma-mobile", "gnome-mobile", "lomiri", "sxmo-de-dwm",
          "sxmo-de-sway", "cage", "buffyboard", "asteroid-launcher"}


def arch_allows(spec: str, arch: str) -> bool:
    """Does an Alpine `arch=` spec permit this architecture?

    The syntax is a token list where `all`/`noarch` mean everything and a `!`
    prefix excludes: `arch="noarch !armhf !x86"` means every architecture
    except armhf and x86. Treating it as a plain substring match reports phosh
    as unavailable on aarch64, which is both wrong and obviously wrong -- it is
    what most pmOS phones actually run.
    """
    if not spec or not arch:
        return True
    tokens = spec.split("#", 1)[0].split()
    excluded = {t[1:] for t in tokens if t.startswith("!")}
    if arch in excluded:
        return False
    included = {t for t in tokens if not t.startswith("!")}
    if included & {"all", "noarch"}:
        return True
    return arch in included


def current() -> str:
    try:
        proc = subprocess.run(["pmbootstrap", "config", "ui"],
                              capture_output=True, text=True, timeout=20)
        return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def cmd_ui(args, ctx) -> int:
    pmaports = pmap.find_pmaports(ctx.cfg)
    if not pmaports:
        raise Bail("no pmaports checkout found", EX_FAIL,
                   "porthole init    adopts a checkout or clones one")
    uis = pmap.user_interfaces(pmaports)
    if not uis:
        raise Bail(f"no postmarketos-ui-* packages under {pmaports}/main",
                   EX_FAIL)
    now = current()
    arch = ctx.cfg.get("PORTHOLE_ARCH", "")

    for ui in uis:
        ui["mobile"] = ui["name"] in MOBILE
        ui["current"] = ui["name"] == now
        ui["available"] = arch_allows(ui["arch"], arch)

    if not args.name:
        shown = [u for u in uis if args.all or u["mobile"] or u["current"]]

        def render():
            o = ctx.out
            o.heading("user interfaces" + ("" if args.all else " (mobile)"))
            o.blank()
            width = max(len(u["name"]) for u in shown)
            for ui in shown:
                mark = o.mark("active", ui["current"])
                note = "" if ui["available"] else o.paint("  [not for your arch]", "red")
                o(f" {mark} {ui['name']:<{width}}  "
                  f"{o.paint(ui['description'][:56], 'grey')}{note}")
            o.blank()
            o(f"  current: {o.paint(now or 'unknown', 'bold')}")
            if not args.all:
                o(f"  {len(uis) - len(shown)} more (desktop/appliance): "
                  f"`porthole ui --all`")
            o.blank()
            o.hint("porthole ui <name>   switch")
        return ctx.emit(uis if args.json else shown, render)

    match = next((u for u in uis if u["name"] == args.name), None)
    if match is None:
        near = [u["name"] for u in uis if args.name in u["name"]]
        raise Bail(f"no UI {args.name!r}", EX_FAIL,
                   f"did you mean: {', '.join(near[:4])}?" if near
                   else "`porthole ui --all` lists them")
    if match["current"]:
        ctx.out(f"already on {args.name}.")
        return EX_OK
    if not match["available"]:
        raise Bail(f"{args.name} does not build for {arch}", EX_FAIL,
                   f"its arch is {match['arch']!r}")

    ctx.out.heading(f"switch {now or '?'} -> {args.name}")
    ctx.out(f"  {match['description']}")
    ctx.out.blank()
    ctx.out(ctx.out.paint(
        "  Changing the UI rebuilds the rootfs. The device keeps running the\n"
        "  old one until you reinstall or reflash.", "yellow"))
    ctx.out.blank()
    if not args.yes:
        ctx.out.hint(f"porthole ui {args.name} --yes    to go ahead")
        return EX_OK

    rc = subprocess.run(["pmbootstrap", "config", "ui", args.name]).returncode
    if rc != 0:
        raise Bail("pmbootstrap refused the UI change", EX_FAIL)
    ctx.out(ctx.out.paint(f"  ui is now {args.name}", "green"))
    ctx.out.hint("pmbootstrap install   # rebuild the rootfs with it")
    return EX_OK


SPEC = {
    "verb": "ui",
    "order": 45,
    "help": "see and switch the compositor / desktop",
    "description": (
        "pmOS ships 20-odd user interfaces. This lists the ones built for a\n"
        "handset by default, flags any that will not build for your\n"
        "architecture, and switches between them."),
    "args": [
        (["name"], {"nargs": "?", "help": "UI to switch to"}),
        (["--all"], {"action": "store_true", "help": "include desktop shells"}),
        (["--yes"], {"action": "store_true", "help": "actually switch"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_ui,
    "examples": ["porthole ui", "porthole ui --all",
                 "porthole ui plasma-mobile --yes"],
}
