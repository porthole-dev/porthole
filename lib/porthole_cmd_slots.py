#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole slots` -- A/B slot policy, probed from the device.

WHY
    The `slot-policy` milestone must be settled before anything is written to
    the phone, and the only honest source is the bootloader. Until now the
    hint pointed at `porthole new-device <codename> --from-fastboot` -- a
    device-CREATION verb -- for a profile that already existed. The redfin
    (Pixel 5) port stopped exactly there.

THE RULE THIS VERB EXISTS TO KEEP
    A value the device did not report stays UNSET. The redfin session got this
    right by hand and said so in its own retrospective: it did not invent A/B
    state, did not invent a DTBO requirement, did not force an active slot.
    Guessing an active slot is how a port writes to the wrong one, and the
    wrong one on a phone with no known-good image is a brick.
"""
from __future__ import annotations

import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_STATE, EX_UNAVAILABLE

_PREFIX = "(bootloader) "


def parse_getvar(text: str) -> dict:
    """`fastboot getvar all` output as a dict. Pure, so it is testable."""
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(_PREFIX):
            continue
        body = line[len(_PREFIX):]
        if ": " not in body:
            continue
        key, value = body.split(": ", 1)
        found[key.strip()] = value.strip()
    return found


def slot_policy(variables: dict) -> dict:
    """Profile settings derivable from what the bootloader reported.

    Only settings it ACTUALLY reported. An absent key produces an absent
    setting, never a default.
    """
    policy = {}
    count = variables.get("slot-count")
    if count:
        policy["PORTHOLE_HAS_AB_SLOTS"] = "1" if count.strip() not in ("0", "1") else "0"
    current = variables.get("current-slot")
    if current and policy.get("PORTHOLE_HAS_AB_SLOTS") == "1":
        policy["PORTHOLE_ACTIVE_SLOT"] = current
    for slot in ("a", "b"):
        if variables.get(f"slot-unbootable:{slot}") == "yes":
            policy["PORTHOLE_SLOT_FORBIDDEN"] = slot
    retry = variables.get("slot-retry-count:" + (current or ""))
    if retry:
        policy["PORTHOLE_SLOT_RETRY_COUNT"] = retry
    return policy


def update_env(text: str, values: dict) -> str:
    """Set keys in a device.env, preserving everything else.

    A profile is a documented file: the comments beside a value are load
    bearing (`PORTHOLE_SLOT_FORBIDDEN="a"   # slot a has no good image`), so
    rewriting it wholesale would destroy the reason the value is what it is.
    An existing key is replaced in place; a new one is appended.
    """
    lines = text.splitlines()
    remaining = dict(values)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            comment = ""
            if "#" in line:
                comment = "   " + line[line.index("#"):]
            lines[i] = f'{key}="{remaining.pop(key)}"{comment}'
    if remaining:
        lines.append("")
        lines.append("# --- probed by `porthole slots probe` ---")
        for key, value in sorted(remaining.items()):
            lines.append(f'{key}="{value}"')
    return "\n".join(lines) + "\n"


def _probe(ctx, args) -> int:
    # `fastboot getvar all` BLOCKS waiting for a device instead of failing
    # when nothing is attached, so probing it first made this verb hang for
    # a full minute before admitting there was no phone -- a verb that
    # appears to hang is one people ctrl-C and stop trusting. `fastboot
    # devices` answers instantly, and it is not merely faster: it is the
    # authoritative check. lsusb mislabels the running gadget as fastboot
    # (18d1:d001) -- only `fastboot devices` discriminates, per
    # profiles/google-taimen/device.env.
    try:
        listed = subprocess.run(["fastboot", "devices"],
                                capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        # 69, not 1: the check did not happen. 1 would say the slots are bad.
        raise Bail("fastboot is not installed", EX_UNAVAILABLE,
                   "`porthole doctor` names how to install it") from None
    except subprocess.SubprocessError as exc:
        raise Bail(f"fastboot failed: {exc}", EX_FAIL) from None
    if not (listed.stdout or "").strip():
        raise Bail("no device is in fastboot", EX_STATE,
                   "`fastboot devices` lists nothing. Note lsusb mislabels the "
                   "running gadget as fastboot -- only `fastboot devices` "
                   "discriminates (profiles/google-taimen/device.env)")

    try:
        proc = subprocess.run(["fastboot", "getvar", "all"],
                              capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise Bail("fastboot is not installed", EX_UNAVAILABLE,
                   "`porthole doctor` names how to install it") from None
    except subprocess.SubprocessError as exc:
        raise Bail(f"fastboot failed: {exc}", EX_FAIL) from None

    # fastboot writes getvar output to stderr, which is not a mistake to
    # correct: read both and let the parser ignore what is not a variable.
    variables = parse_getvar((proc.stdout or "") + (proc.stderr or ""))
    if not variables:
        raise Bail("the device reported no variables", EX_FAIL,
                   "is it in fastboot? `fastboot devices` discriminates -- "
                   "lsusb mislabels the running gadget")

    policy = slot_policy(variables)
    unknown = [name for name in ("PORTHOLE_HAS_AB_SLOTS", "PORTHOLE_ACTIVE_SLOT")
               if name not in policy]

    def render():
        ctx.out.heading("slot policy read from the device")
        for key, value in sorted(policy.items()):
            ctx.out.kv(key, value, 26)
        for name in unknown:
            ctx.out.kv(name, "NOT REPORTED — left unset", 26)
        if not args.yes:
            ctx.out.blank()
            ctx.out(ctx.out.paint(
                "  nothing was written — add --yes to save this to the profile",
                "grey"))

    rc = ctx.emit(policy, render)
    if args.yes and policy:
        path = (ctx.root / "profiles"
                / str(ctx.cfg.get("PORTHOLE_DEVICE")) / "device.env")
        path.write_text(update_env(path.read_text(), policy))
        ctx.out(ctx.out.paint(f"  wrote {len(policy)} value(s) to {path}",
                              "green"))
    return rc if rc == EX_OK else rc


def cmd_slots(args, ctx) -> int:
    return _probe(ctx, args)


SPEC = {
    "verb": "slots",
    "order": 16,
    "help": "read A/B slot policy from the device, never guess it",
    "description": (
        "Slot policy must be settled before anything is written to the\n"
        "phone, and the only honest source is the bootloader.\n\n"
        "A value the device does not report is left UNSET and reported as\n"
        "unset. Guessing an active slot is how a port writes to the wrong\n"
        "one, and on a phone with no known-good image that is a brick."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "default": "probe",
                      "choices": ["probe"], "help": "probe"}),
        (["--yes"], {"action": "store_true",
                     "help": "write the probed values into the profile"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_slots,
    "examples": [
        "porthole slots probe",
        "porthole slots probe --yes",
        "porthole slots probe --json",
    ],
}
