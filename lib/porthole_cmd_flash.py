#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole flash` -- write a boot image, under the slot policy.

The most irreversible thing this toolkit does, and it had two defects:

  - `tkflash-boot` (boot partition only) existed in tools/ph-build.sh and no
    verb could reach it -- only `tkflash` (rootfs AND boot) was wired up, so
    the only flash available replaced everything. `action` picks between
    them now; `boot` is the default because it is the one that does not cost
    a rootfs rebuild.
  - the preview refused with EX_STATE when the device was not in the state
    the flash needs, which made `porthole flash` with no `--yes` -- the thing
    you run to find out what would happen -- unavailable exactly when you are
    deciding. The preview now always renders; only an actual write is gated
    on the device being ready.

Two gates remain, both refusals rather than warnings:

  - `--yes` (and, for `full`, also `--replace-rootfs`) is required to write
    anything. Nothing here is undoable from the bootloader.
  - arming a slot the profile forbids is refused outright, because recovery
    from the bootloader cannot re-arm a slot. That is the last moment at which
    the mistake is still cheap.
"""
from __future__ import annotations

import sys

import porthole_plan as plan
from porthole_cli import Bail, EX_FAIL, EX_OK, EX_STATE, EX_USAGE, gate_flag
from porthole_cmd_build import _run

# The verb -> ph-build.sh function it runs. `boot` leaves the rootfs alone;
# `full` is tkflash, which flashes the rootfs THEN calls tkflash-boot itself
# (tools/ph-build.sh:1397-1406).
FUNCS = {"boot": "tkflash-boot", "full": "tkflash"}


def cmd_flash(args, ctx) -> int:
    cfg = ctx.cfg
    device = cfg.get("PORTHOLE_DEVICE", "")
    target = args.slot or cfg.get("PORTHOLE_ACTIVE_SLOT", "")
    forbidden = (cfg.get("PORTHOLE_SLOT_FORBIDDEN", "") or "").split()
    has_slots = cfg.get("PORTHOLE_HAS_AB_SLOTS") == "1"

    # Unconditional, before anything else -- including the preview. Recovery
    # from the bootloader cannot re-arm a slot, so an operator must not be
    # able to reach this by adding --yes to a command they only meant to
    # preview.
    if has_slots and target and target in forbidden:
        raise Bail(
            f"slot {target!r} is listed in PORTHOLE_SLOT_FORBIDDEN", EX_STATE,
            "that slot has no known-good image; arming it means a device that "
            "cannot boot and cannot be re-armed from the bootloader")
    if has_slots and not cfg.get("PORTHOLE_SLOTS_PROBED"):
        ctx.out.warn("slots were never probed on this device — "
                     "HAS_AB_SLOTS may be the shipped default")
        # Same stream as the warning above, not the preview's stdout: `2>log`
        # used to capture the warning and lose the hint that explains it.
        ctx.out.hint("porthole slots probe", "reads it from the bootloader",
                     stream=sys.stderr)

    action = getattr(args, "action", None) or "boot"
    op = plan.op(f"flash-{action}")
    flag = gate_flag(op)   # "--yes" for boot, "--replace-rootfs" for full

    # Probed rather than deferred: the preview shows the CURRENT state as a
    # row, and that has always meant probing it. What changed is that a
    # mismatch is reported here, not raised -- see `problems` below.
    state = ctx.device().state()
    # Flash needs only the DTB fact and the device state -- porthole_sites is
    # for the ops that BUILD an image (kernel tree, work dir, rootfs
    # password); pulling that machinery in here for two facts would make a
    # flash preview depend on the sandbox/workspace being resolvable at all.
    facts = {"state": state, plan.DTB: cfg.get("PORTHOLE_DTB", "")}
    problems = plan.unmet(op, facts)

    def render():
        o = ctx.out
        o.heading("would flash")
        o.kv("action", action, 14)
        o.kv("device", device, 14)
        o.kv("state", state, 14)
        o.kv("slots", "A/B" if has_slots else "single", 14)
        o.kv("set active", target or o.paint("left alone", "grey"), 14)
        o.kv("forbidden", " ".join(forbidden) or o.paint("none", "grey"), 14)
        o.kv("dtbo", cfg.get("PORTHOLE_DTBO_IMG", "")
             or o.paint("none", "grey"), 14)
        if problems:
            o.blank()
            o.heading("not ready")
            for p in problems:
                o(o.paint(f"  - {p}", "yellow"))
            if state and state != plan.FASTBOOT:
                o.hint("tools/ph-to-fastboot.sh",
                       "reboot it to the bootloader")
        o.blank()
        o(o.paint("  A bad image on the wrong slot leaves a device that "
                  "will not boot.", "yellow"))
        o.blank()
        needed = ["--yes"] + (["--replace-rootfs"] if flag == "--replace-rootfs"
                              else [])
        o.hint(f"porthole flash {action} {' '.join(needed)}")

    if not args.yes:
        return ctx.emit({"action": action, "device": device, "state": state,
                         "slot": target, "forbidden": forbidden,
                         "problems": problems, "would_run": True}, render)

    # `--yes` alone confirms a `boot` flash but not a `full` one -- replacing
    # the rootfs is not a --yes-shaped decision, and the flag that names the
    # loss is required in addition. Refused here, loudly, rather than quietly
    # falling back to a preview: an operator who typed --yes meant to run
    # something.
    if flag == "--replace-rootfs" and not getattr(args, "replace_rootfs", False):
        raise Bail(
            "a full flash replaces the rootfs and every locally-installed "
            "package -- that needs --replace-rootfs as well as --yes",
            EX_USAGE, "porthole flash full --yes --replace-rootfs")

    # --force means "I know the state probe is wrong", same as before this
    # rework -- it does not waive a missing DTB or any other real
    # prerequisite, only the device-state mismatch.
    gate_state = state
    if args.force and op.needs_state in (plan.BOOTED, plan.FASTBOOT):
        gate_state = op.needs_state
    gate_problems = plan.unmet(op, {**facts, "state": gate_state})
    if gate_problems:
        raise Bail("this device is not ready to flash", EX_STATE,
                   "; ".join(gate_problems))

    # `rung=op.name` ("flash-boot"/"flash-full"), not the default -- without
    # it `_run` falls back to `rung or func` and the LOG, THE STATUS FILE,
    # THE PROGRESS BAR and this failure message all show `tkflash`, the name
    # of a shell function in tools/ph-build.sh, not an operation anyone typed
    # or would recognise.
    rc = _run(ctx, FUNCS[action], args.timeout, rung=op.name)
    if rc != 0:
        raise Bail(f"{op.name} failed", EX_FAIL,
                   "the device may be part-flashed; check it before rebooting")
    ctx.out(ctx.out.paint("  flashed", "green"))
    return EX_OK


SPEC = {
    "verb": "flash",
    "order": 37,
    "group": "build",
    "help": "flash the built boot image, honouring the slot policy",
    "description": (
        "`boot` (default) flashes the boot partition only and leaves the\n"
        "rootfs alone. `full` also replaces the rootfs and every\n"
        "locally-installed package, and additionally needs --replace-rootfs.\n\n"
        "The preview (no --yes) always renders, including while the device\n"
        "is in the wrong state -- it names what is missing rather than\n"
        "refusing to say. Refuses to arm a slot the profile lists as\n"
        "forbidden: recovery from the bootloader cannot re-arm a slot, so\n"
        "this is the last point at which that mistake is cheap."),
    "escapes_scope": True,
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "default": "boot",
                      "choices": ["boot", "full"],
                      "help": "boot: boot partition only (default); "
                              "full: rootfs and boot"}),
        (["--slot"], {"metavar": "SLOT",
                      "help": "slot to arm (default PORTHOLE_ACTIVE_SLOT)"}),
        (["--force"], {"action": "store_true",
                       "help": "flash even if the state probe disagrees"}),
        (["--timeout"], {"type": int, "default": 1800, "metavar": "SEC",
                         "help": "seconds before giving up"}),
        (["--yes"], {"action": "store_true", "help": "actually flash"}),
        (["--replace-rootfs"], {"action": "store_true",
                                "help": "confirm `full` may replace the "
                                        "rootfs and every locally-installed "
                                        "package"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_flash,
    "examples": [
        "porthole flash",
        "porthole flash boot --yes",
        "porthole flash full --yes --replace-rootfs",
        "porthole flash boot --slot a --yes",
    ],
}
