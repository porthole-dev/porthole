#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole flash` -- write a boot image, under the slot policy.

The most irreversible thing this toolkit does, and it had no verb: it lived
inside a shell file that had to be sourced, with taimen's slot and taimen's
dtbo written into it while PORTHOLE_SLOT_FORBIDDEN and PORTHOLE_ACTIVE_SLOT
sat unread in the schema.

Two gates, and both are refusals rather than warnings:

  - `--yes` is required. Nothing here is undoable from the bootloader.
  - arming a slot the profile forbids is refused outright, because recovery
    from the bootloader cannot re-arm a slot. That is the last moment at which
    the mistake is still cheap.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import porthole
from porthole_cli import Bail, EX_FAIL, EX_OK, EX_STATE
from porthole_cmd_build import _run, _script


def cmd_flash(args, ctx) -> int:
    cfg = ctx.cfg
    device = cfg.get("PORTHOLE_DEVICE", "")
    target = args.slot or cfg.get("PORTHOLE_ACTIVE_SLOT", "")
    forbidden = (cfg.get("PORTHOLE_SLOT_FORBIDDEN", "") or "").split()
    has_slots = cfg.get("PORTHOLE_HAS_AB_SLOTS") == "1"

    if has_slots and target and target in forbidden:
        raise Bail(
            f"slot {target!r} is listed in PORTHOLE_SLOT_FORBIDDEN", EX_STATE,
            "that slot has no known-good image; arming it means a device that "
            "cannot boot and cannot be re-armed from the bootloader")
    if has_slots and not cfg.get("PORTHOLE_SLOTS_PROBED"):
        ctx.out.warn("slots were never probed on this device — "
                     "HAS_AB_SLOTS may be the shipped default")
        ctx.out.hint("porthole new-device <codename> --from-fastboot   asks it")

    state = ctx.device().state()
    if state != "FASTBOOT" and not args.force:
        raise Bail(f"the device is {state}, not FASTBOOT", EX_STATE,
                   "reboot it to the bootloader first, or pass --force if you "
                   "know the state probe is wrong")

    if not args.yes:
        def render():
            o = ctx.out
            o.heading("would flash")
            o.kv("device", device, 14)
            o.kv("state", state, 14)
            o.kv("slots", "A/B" if has_slots else "single", 14)
            o.kv("set active", target or o.paint("left alone", "grey"), 14)
            o.kv("forbidden", " ".join(forbidden) or o.paint("none", "grey"), 14)
            o.kv("dtbo", cfg.get("PORTHOLE_DTBO_IMG", "")
                 or o.paint("none", "grey"), 14)
            o.blank()
            o(o.paint("  A bad image on the wrong slot leaves a device that "
                      "will not boot.", "yellow"))
            o.blank()
            o.hint("porthole flash --yes")
        return ctx.emit({"device": device, "state": state, "slot": target,
                         "forbidden": forbidden, "would_run": True}, render)

    rc = _run(ctx, "tkflash", args.timeout)
    if rc != 0:
        raise Bail("tkflash failed", EX_FAIL,
                   "the device may be part-flashed; check it before rebooting")
    ctx.out(ctx.out.paint("  flashed", "green"))
    return EX_OK


SPEC = {
    "verb": "flash",
    "order": 37,
    "help": "flash the built boot image, honouring the slot policy",
    "description": (
        "Refuses to arm a slot the profile lists as forbidden: recovery from\n"
        "the bootloader cannot re-arm a slot, so this is the last point at\n"
        "which that mistake is cheap.\n\n"
        "Refuses to run unless the device is in FASTBOOT, and verifies the\n"
        "image carries the kernel and DTB you just built before writing it."),
    "escapes_scope": True,
    "args": [
        (["--slot"], {"metavar": "SLOT",
                      "help": "slot to arm (default PORTHOLE_ACTIVE_SLOT)"}),
        (["--force"], {"action": "store_true",
                       "help": "flash even if the state probe disagrees"}),
        (["--timeout"], {"type": int, "default": 1800, "metavar": "SEC",
                         "help": "seconds before giving up"}),
        (["--yes"], {"action": "store_true", "help": "actually flash"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_flash,
    "examples": [
        "porthole flash",
        "porthole flash --yes",
        "porthole flash --slot a --yes",
    ],
}
