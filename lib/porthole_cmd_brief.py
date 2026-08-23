# SPDX-License-Identifier: MIT
"""`porthole brief` -- everything an agent needs to start, in one call.

An LLM opening this repo otherwise has to guess: which device, is it reachable,
what tools exist, what rules apply, what did the last session leave behind. That
is four or five exploratory commands, each of which can be got wrong, before any
work starts.

`porthole brief` answers all of it at once, and `--json` makes it parseable.
This is the single command AGENTS.md tells an agent to run first.

Deliberately read-only and fast: it must be safe to run at the start of every
session, including on a host with no device attached.
"""
from __future__ import annotations

import pathlib

import porthole
from porthole_cli import EX_OK, version

# The rules an agent gets wrong most often, in the order they cause damage.
# Kept short on purpose: a wall of text gets skimmed, and these five have each
# cost a real session.
RULES = [
    ("Never hand-roll what a tool does",
     "writing `ssh ... reboot` or `sleep 60` means you have not found the tool "
     "yet. `porthole tools --grep <what>`."),
    ("Take the device mutex, declaring the state you need",
     "TK_AGENT=<you> tools/tk-device.sh --need-booted <cmd>. "
     "Exit 75 = retry. Exit 76 = do NOT retry, something must move the device."),
    ("Found the device in a state you did not set? Say so and hand back",
     "it is usually someone else's measurement in progress, not a fault."),
    ("Prove the code under test actually ran",
     "decide which number is your control BEFORE the run. A null from a path "
     "that never executed is not a refutation."),
    ("Confirm before anything irreversible",
     "flashing, thermal ramps, anything that can leave a slot unbootable."),
    ("Contribute what you learn back to brain/",
     "this project exists to share knowledge, not just to fix one phone. If "
     "you established something 100% and it would have saved someone a "
     "session: `porthole brain --new <id>`, then `--lint`, then `--submit`. "
     "A session that learned something and wrote nothing down is unfinished."),
]


def cmd_brief(args, ctx) -> int:
    root = ctx.root
    cfg = ctx.cfg
    device = cfg.get("PORTHOLE_DEVICE", "")

    import porthole_cmd_tools as tmod
    tools = tmod.collect(root, device)

    state = "not probed"
    if not args.no_device:
        try:
            state = ctx.device().state()
        except Exception as exc:  # noqa: BLE001
            state = f"probe failed: {exc}"

    laws = _notes(root, "laws")
    profile_gaps = [k for k in ("PORTHOLE_SOC", "PORTHOLE_ARCH",
                                "PORTHOLE_REBOOT_BUDGET_S")
                    if not cfg.get(k)]

    payload = {
        "porthole_version": version(root),
        "root": str(root),
        "device": {
            "codename": device,
            "name": cfg.get("PORTHOLE_DEVICE_NAME", ""),
            "soc": cfg.get("PORTHOLE_SOC", ""),
            "state": state,
            "ssh_target": cfg.get("PHONE", ""),
            "profile_gaps": profile_gaps,
            "traps": _device_traps(cfg),
        },
        "tools": {
            "count": len(tools),
            "undocumented": [t.name for t in tools if t.gaps],
            "catalogue": "porthole tools --json",
        },
        "brain": {
            "laws": laws,
            "search": "porthole brain <query> [--scope <scope>]",
            "index": "brain/INDEX.md",
            "contribute": {
                "duty": "If you establish something validated that would have "
                        "saved someone a session, it belongs in brain/. This "
                        "is part of the work, not an optional extra.",
                "new": "porthole brain --new <kebab-id> --severity trap",
                "lint": "porthole brain --lint",
                "submit": "porthole brain --submit",
                "bar": "one idea per note; cite evidence a stranger can "
                       "re-check; scope it honestly.",
            },
        },
        "rules": [{"rule": r, "why": w} for r, w in RULES],
        "entrypoints": {
            "agents": "AGENTS.md",
            "humans": "README.md",
            "device_protocol": "brain/playbooks/00-device-protocol.md",
            "contributing": "docs/CONTRIBUTING.md",
        },
        "next": _next_steps(cfg, device, state, profile_gaps),
    }

    def render():
        ctx.out.heading(f"porthole {payload['porthole_version']} — session brief")
        ctx.out.blank()
        w = 12
        ctx.out.kv("device", f"{device or '(none)'} "
                             f"{cfg.get('PORTHOLE_DEVICE_NAME', '')}".strip(), w)
        ctx.out.kv("soc", cfg.get("PORTHOLE_SOC", "") or "-", w)
        ctx.out.kv("ssh", cfg.get("PHONE", ""), w)
        colour = {"BOOTED": "green", "FASTBOOT": "yellow",
                  "FROZEN": "yellow", "ABSENT": "grey"}.get(state, "grey")
        ctx.out.kv("state", ctx.out.paint(state, colour), w)
        ctx.out.kv("tools", str(len(tools)), w)
        if payload["device"]["traps"]:
            ctx.out.blank()
            ctx.out.heading("device traps encoded in the profile")
            for trap in payload["device"]["traps"]:
                ctx.out(f"  {ctx.out.sym('•', '-')} {trap}")
        ctx.out.blank()
        ctx.out.heading("rules that cost sessions when broken")
        for rule, why in RULES:
            ctx.out(f"  {ctx.out.sym('•', '-')} {ctx.out.paint(rule, 'bold')}")
            ctx.out(f"    {ctx.out.paint(why, 'grey')}")
        ctx.out.blank()
        ctx.out.heading("read next")
        for label, path in payload["entrypoints"].items():
            ctx.out.kv(label, path, w)
        ctx.out.blank()
        ctx.out.heading("suggested next steps")
        for step in payload["next"]:
            ctx.out.hint(step)

    return ctx.emit(payload, render)


def _notes(root: pathlib.Path, section: str) -> list[dict]:
    try:
        import porthole_cmd_brain as porthole_brain
    except ImportError:
        return []
    return [{"id": n.id, "title": n.title}
            for n in porthole_brain.load_notes(root)
            if n.path.parent.name == section]


def _device_traps(cfg) -> list[str]:
    """Turn the profile's trap flags into sentences an agent will act on."""
    out = []
    if cfg.get("PORTHOLE_PANEL_STAYS_DARK") == "1":
        out.append("The panel never lights: a booted device and a hung one look "
                   "identical. Never judge a boot by the screen.")
    if cfg.get("PORTHOLE_USB_LIES_AS_FASTBOOT") == "1":
        out.append(f"lsusb mislabels the running gadget "
                   f"({cfg.get('PORTHOLE_USB_GADGET_ID', '?')}) as fastboot. "
                   f"Only `fastboot devices` discriminates.")
    if cfg.get("PORTHOLE_REBOOT_MODE_VIA_SYSCALL") == "1":
        out.append("`reboot bootloader` is busybox and discards the mode string. "
                   "Use tools/tk-to-fastboot.sh.")
    if cfg.get("PORTHOLE_SLOT_FORBIDDEN"):
        out.append(f"NEVER set_active {cfg['PORTHOLE_SLOT_FORBIDDEN']} — no "
                   f"known-good image on that slot.")
    if cfg.get("PORTHOLE_BOOT_RETRIES"):
        out.append(f"Every {cfg['PORTHOLE_BOOT_RETRIES']}rd boot lands in the "
                   f"bootloader by itself: a retry countdown, not a glitch. "
                   f"set_active resets it.")
    if cfg.get("PORTHOLE_WATCHDOG_MAX_S"):
        out.append(f"Watchdog ceiling is {cfg['PORTHOLE_WATCHDOG_MAX_S']}s. Above "
                   f"it the timeout does not clamp — it DISARMS the watchdog.")
    if cfg.get("PORTHOLE_NEEDS_DTBO") == "1":
        out.append("The bootloader reads a dtbo from the ACTIVE SLOT; the "
                   "mainline and stock overlays are mutually exclusive. A "
                   "mismatch bounces to fastboot in ~3s and looks like a bad "
                   "kernel.")
    return out


def _next_steps(cfg, device: str, state: str, gaps: list[str]) -> list[str]:
    if not device:
        return ["porthole devices", "porthole init --device <codename>",
                "porthole new-device <codename>   # to port something new"]
    steps = []
    if cfg.source("PORTHOLE_USER") == "default":
        steps.append("porthole init   # nothing set a username yet")
    if state == "ABSENT":
        steps.append("attach and power the device, then `porthole doctor`")
    elif state == "FASTBOOT":
        steps.append("tools/tk-reboot.sh   # leave the bootloader, re-arming the slot")
    elif state == "FROZEN":
        steps.append("tools/tk-recover.sh   # kernel alive, userspace gone")
    elif state == "BOOTED":
        steps.append("porthole doctor   # confirm sudo -n works before anything else")
    if gaps:
        steps.append(f"fill profiles/{device}/device.env: {', '.join(gaps)}")
    steps.append("porthole brain --severity law   # ten notes, read once")
    return steps


SPEC = {
    "verb": "brief",
    "order": 15,
    "help": "everything an agent needs to start a session, in one call",
    "description": (
        "Which device, is it reachable, what tools exist, what rules apply,\n"
        "what this device's encoded traps are, and what to do next.\n\n"
        "Read-only and safe to run at the start of every session. `--json` for\n"
        "an agent; this is what AGENTS.md says to run first."),
    "args": [
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
        (["--no-device"], {"action": "store_true",
                           "help": "skip the device probe (faster, offline)"}),
    ],
    "run": cmd_brief,
    "examples": [
        "porthole brief",
        "porthole brief --json         # for an agent",
        "porthole brief --no-device    # offline",
    ],
}
