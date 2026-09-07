# SPDX-License-Identifier: MIT
"""`porthole devices` -- which device profiles exist."""
from __future__ import annotations

import pathlib

import porthole
from porthole_cli import EX_OK


def summarise(root: pathlib.Path, codename: str, active: str) -> dict:
    path = root / "profiles" / codename / "device.env"
    vals = porthole.parse_env(path.read_text()) if path.is_file() else {}
    total = sum(1 for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")
                and "=" in line) if path.is_file() else 0
    filled = sum(1 for v in vals.values() if v)
    return {
        "codename": codename,
        "name": vals.get("PORTHOLE_DEVICE_NAME", ""),
        "vendor": vals.get("PORTHOLE_VENDOR", ""),
        "soc": vals.get("PORTHOLE_SOC", ""),
        "active": codename == active,
        "keys_filled": filled,
        "keys_total": total,
        "tools": len(list((root / "profiles" / codename / "tools").glob("*")))
        if (root / "profiles" / codename / "tools").is_dir() else 0,
    }


def cmd_devices(args, ctx) -> int:
    root = ctx.root
    try:
        active = ctx.cfg.get("PORTHOLE_DEVICE", "")
    except Exception:  # noqa: BLE001  -- listing must work with a broken profile
        active = ""
    rows = [summarise(root, n, active) for n in porthole.list_profiles(root)]

    def render():
        if not rows:
            ctx.out("no device profiles yet.")
            ctx.out.hint("porthole new-device <codename>", "to start one")
            return
        width = max(len(r["codename"]) for r in rows)
        swidth = max((len(r["soc"]) for r in rows), default=4)
        for row in rows:
            mark = ctx.out.mark("active", row["active"])
            done = f"{row['keys_filled']}/{row['keys_total']} keys"
            ctx.out(f" {mark} {row['codename']:<{width}}  {row['soc']:<{swidth}}  "
                    f"{row['name']}  {ctx.out.paint(done, 'grey')}")
        ctx.out.blank()
        ctx.out(ctx.out.paint(
            f"{ctx.out.sym('●', '*')} = active. "
            f"`porthole -d <codename> <verb>` to act on another.", "grey"))

    return ctx.emit(rows, render)


SPEC = {
    "verb": "devices",
    "order": 40,
    "group": "start",
    "help": "list device profiles",
    "description": "Every device profile in profiles/, and how complete it is.",
    "args": [(["--json"], {"action": "store_true", "help": "machine-readable"})],
    "run": cmd_devices,
    "examples": ["porthole devices", "porthole devices --json"],
}
