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
     "session: `porthole brain new <id>`, then `--lint`, then `--submit`. "
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
            # A display verdict: 30s stale is fine, 6.3s of waiting is not.
            state = ctx.device().state(max_age=30)
        except Exception as exc:  # noqa: BLE001
            state = f"probe failed: {exc}"

    laws = _notes(root, "laws")
    profile_gaps = [k for k in ("PORTHOLE_SOC", "PORTHOLE_ARCH",
                                "PORTHOLE_REBOOT_BUDGET_S")
                    if not cfg.get(k)]

    _laws, _findings = _from_brain(pathlib.Path(ctx.root), device,
                                   cfg.get("PORTHOLE_SOC", ""))
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
                "new": "porthole brain new <kebab-id> --severity trap",
                "lint": "porthole brain lint",
                "submit": "porthole brain submit",
                "bar": "one idea per note; cite evidence a stranger can "
                       "re-check; scope it honestly.",
            },
        },
        "rules": [{"rule": r, "why": w} for r, w in RULES],
        "laws": _laws,
        "findings": _findings,
        "entrypoints": {
            "agents": "AGENTS.md",
            "humans": "README.md",
            "device_protocol": "brain/playbooks/00-device-protocol.md",
            "contributing": "docs/CONTRIBUTING.md",
        },
        # NOTE: this is a list of suggestion STRINGS. The port's next
        # milestone is an object at payload["port"]["next"]. Same word, one
        # level apart, different types -- kept because both names are correct
        # in place and renaming either would break a consumer, but an agent
        # reading this file should know before it indexes the wrong one.
        "next": _next_steps(cfg, device, state, profile_gaps),
        # The port's own state, folded in so the ONE call AGENTS.md tells an
        # agent to make first also answers "where am I". A second call for the
        # most important question would be a second call most sessions skip.
        "port": _port_state(ctx, device),
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
                  "FROZEN": "yellow", "INITRAMFS": "yellow",
                  "ABSENT": "grey"}.get(state, "grey")
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
        if _laws:
            ctx.out.blank()
            ctx.out.heading(f"the {len(_laws)} laws — read once, properly")
            for law in _laws:
                ctx.out(f"  {ctx.out.sym('•', '-')} {law['law']}")
            ctx.out(ctx.out.paint("    porthole brain <id>   to read one in full",
                                  "grey"))
        if _findings:
            ctx.out.blank()
            _n = len(_findings)
            ctx.out.heading(f"already answered on this port — {_n} "
                            f"{'finding' if _n == 1 else 'findings'}")
            ctx.out(ctx.out.paint(
                "  Read these BEFORE forming a theory. Each one closes a "
                "question and names\n  the ideas it kills; re-deriving one has "
                "already cost a day.", "grey"))
            for f in _findings:
                ctx.out(f"  {ctx.out.sym('•', '-')} {f['finding']}")
                ctx.out(f"    {ctx.out.paint('porthole brain ' + f['id'], 'grey')}")
        ctx.out.blank()
        ctx.out.heading("read next")
        for label, path in payload["entrypoints"].items():
            ctx.out.kv(label, path, w)
        ctx.out.blank()
        port = payload["port"]
        if port and port.get("next"):
            ctx.out.heading("where this port is")
            pr = port["progress"]
            ctx.out.kv("progress", f"{pr['done']}/{pr['total']} · {pr['phase']}", w)
            ctx.out.kv("next", port["next"]["title"], w)
            if port["next"]["command"]:
                ctx.out.kv("", ctx.out.paint(port["next"]["command"], "cyan"), w)
            for item in port.get("stale", [])[:3]:
                ctx.out.warn(f"{item['title']}: {item['detail']}")
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


def _port_state(ctx, device: str) -> dict:
    """Milestone state, or {} when there is no device to have state about.

    Never fatal: `brief` is the first thing an agent runs, and a brief that
    dies because a checklist is missing is a brief that stops the session
    before it starts.
    """
    if not device:
        return {}
    try:
        import porthole_cmd_next as nxt
        _, summary, has_markers = nxt.collect(ctx)
        return {**summary, "checklist_has_markers": has_markers}
    except Exception:  # noqa: BLE001
        return {}


def _from_brain(root: pathlib.Path, device: str, soc: str):
    """The laws and the findings, READ from brain/ rather than restated here.

    RULES below is hand-written and stayed that way while brain/laws/ grew to
    ten notes, so the brief showed three of them and silently omitted seven --
    including "read the vendor before inventing a mechanism", which cost a day.
    Writing a law had no effect on what any agent was shown. Generated now, so
    it cannot drift again.

    Findings are surfaced unprompted because that is the whole point of them: a
    question already answered is only useful to someone who has not yet decided
    to go looking.
    """
    try:
        from porthole_cmd_brain import load_notes
        notes = load_notes(root)
    except Exception:  # noqa: BLE001 -- the brief must never fail on brain/
        return [], []
    want = {"generic"}
    if soc:
        want.add(f"soc:{soc}")
    if device:
        want.add(f"device:{device}")
    laws, findings = [], []
    for n in notes:
        sev = n.meta.get("severity", "")
        if sev == "law":
            laws.append({"law": n.title, "id": n.id})
        elif sev == "finding" and n.scope in want:
            findings.append({"finding": n.title, "id": n.id,
                             "refutes": n.meta.get("refutes", "")})
    laws.sort(key=lambda d: d["id"])
    findings.sort(key=lambda d: d["id"])
    return laws, findings


def _next_steps(cfg, device: str, state: str, gaps: list[str]) -> list[str]:
    if not device:
        return ["porthole devices", "porthole init <codename>",
                "porthole new-device <codename>   # to port something new"]
    steps = []
    if cfg.source("PORTHOLE_USER") == "default":
        steps.append("porthole init   # nothing set a username yet")
    if state == "ABSENT":
        steps.append("attach and power the device, then `porthole doctor`")
    elif state == "FASTBOOT":
        steps.append("tools/tk-reboot.sh   # leave the bootloader, re-arming the slot")
    elif state == "INITRAMFS":
        steps.append("tools/tsh.py 'dmesg | grep pmOS-rd'   # why root did not mount")
    elif state == "FROZEN":
        steps.append("tools/tk-recover.sh   # kernel alive, userspace gone")
    elif state == "BOOTED":
        steps.append("porthole doctor   # confirm sudo -n works before anything else")
    if gaps:
        steps.append(f"fill profiles/{device}/device.env: {', '.join(gaps)}")
    steps.append("porthole brain search --severity law   # ten notes, read once")
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
