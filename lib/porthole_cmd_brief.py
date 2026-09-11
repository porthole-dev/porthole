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

import json
import os
import pathlib
import subprocess
import time

import porthole
import porthole_rules as rules
import porthole_secrets as secrets
from porthole_cli import EX_OK, version

# The rules moved to lib/porthole_rules.py, where each one names the check that
# enforces it. They were hand-written here and had drifted from AGENTS.md and
# the skill: this list was missing "never ask for host root" and "never
# hardcode a value", and it is the copy an agent actually executes, because
# section 9 tells it to run `porthole brief --json` first.
#
# Human output still shows the short session set -- a wall of text gets
# skimmed, and that judgement was right. --json serves all of them with their
# levels and enforcers, because a machine does not skim.
RULES = rules.session_rules()


def activity_summary(snap, holder: str = "", alive=None) -> str:
    """One line: is anything building here, and who holds the buildroot.

    Pure, so it is testable without a container. This is the first question
    when picking up a handoff -- two agents collided in this repo on
    2026-08-30 because nothing answered it -- and it used to require
    hand-rolling `podman exec ps` plus a lock probe.
    """
    import porthole_progress as progress

    live = progress.liveness(snap or {}, alive)
    if live == "running":
        target = (snap or {}).get("rung", "?")
        return f"{live}: {target} ({progress.fmt_dur((snap or {}).get('elapsed'))})"
    if holder:
        return f"idle, but the buildroot lock is held by: {holder}"
    return "idle — no package build running, buildroot free"


def _hooks_state(root: pathlib.Path) -> dict:
    """Is this clone's git pointed at .githooks, and does it matter?

    Here for the same reason as _workspace_state: brief is the verb section 9
    tells every agent to run first, and a hook nobody installed enforces
    nothing. Git ignores in-repo hooks until `core.hooksPath` says otherwise,
    so a fresh clone -- which is what an agent usually gets -- has the secret
    scanner and the trailer strip both switched off and no way to notice.

    Reported, not fixed: writing to someone's git config is a change to their
    checkout, and the shared-checkout note in AGENTS.md section 7 is exactly
    about not doing that behind their back.
    """
    try:
        out = subprocess.run(["git", "-C", str(root), "config", "--get",
                              "core.hooksPath"], capture_output=True,
                             text=True).stdout.strip()
    except OSError:
        return {"installed": True, "message": ""}       # no git; nothing to say
    if out == ".githooks":
        return {"installed": True, "message": ""}
    return {"installed": False, "message":
            "this clone does not have the hooks installed, so nothing strips "
            "attribution trailers or scans a commit message for a serial "
            "before it is written. Run `git config core.hooksPath .githooks` "
            "once. CI catches both afterwards, but only after they are pushed."}


def _workspace_state(root: pathlib.Path) -> dict:
    """Is the build workspace usable, and if not, what should be said about it?

    This belongs in `brief` rather than behind `porthole doctor` because brief
    is the verb AGENTS.md tells every agent to run first. An agent that does
    not know the workspace is missing reaches for host sudo instead, which is
    the single thing this whole subsystem exists to prevent.
    """
    try:
        import porthole_cmd_sandbox as sandbox
        state = sandbox._container_state(root)
    except Exception:  # noqa: BLE001
        return {"ready": True, "message": ""}

    if not state["podman"]:
        return {"ready": False, "message":
                "no podman on this host, so there is no build workspace. "
                "ASK the person you are working with to run "
                "`porthole doctor --fix` -- it needs a password you must not "
                "type. Do not use host sudo instead."}
    if not state["image_built"] or not state["container_running"]:
        return {"ready": False, "message":
                "the build workspace is not up. Run `porthole sandbox up` "
                "before building; do not fall back to host sudo."}
    return {"ready": True, "message": ""}


def cmd_brief(args, ctx) -> int:
    root = ctx.root
    cfg = ctx.cfg
    device = cfg.get("PORTHOLE_DEVICE", "")

    import porthole_cmd_tools as tmod
    tools = tmod.collect(root, device)

    workspace = _workspace_state(root)
    hooks = _hooks_state(root)
    import porthole as _porthole
    drifts = _porthole.drift(cfg)

    state = "not probed"
    kernel = {}
    if not args.no_device:
        try:
            # A display verdict: 30s stale is fine, 6.3s of waiting is not.
            state = ctx.device().state(max_age=30)
        except Exception as exc:  # noqa: BLE001
            state = f"probe failed: {exc}"
        # Fetched HERE, once, beside the device probe, and written to the run
        # dir for the milestone probe to read. Milestone probes must not touch
        # the device: `brief --no-device` has to work offline.
        if state == "BOOTED":
            try:
                import porthole_cmd_build as _build
                import porthole_provenance as prov

                info = prov.running(ctx.device(), cfg.get("PORTHOLE_KERNEL_PKG", ""))
                verdict, evidence = prov.compare(info, *_build.aport_version(ctx))
                # "at" is what lets probe_kernel_provenance() age this claim
                # out: without it a "done" read here kept printing verbatim
                # forever, including past a reflash that made it false.
                kernel = {"state": verdict, "evidence": evidence,
                          "build_version": info.get("build_version", ""),
                          "at": time.time()}
                _write_run_json(ctx, "kernel-provenance.json", kernel)
            except Exception:  # noqa: BLE001 -- brief must never fail on this
                kernel = {}

    # The first question anyone picking up a handoff asks: is a build
    # running, and who holds the buildroot. Two agents collided in this repo
    # on 2026-08-30 because nothing answered it, and answering it by hand
    # meant `podman exec ps` plus a lock probe.
    import porthole_buildroot as buildroot

    rundir = pathlib.Path(cfg.get("PORTHOLE_RUNDIR") or (root / ".run"))
    try:
        pkg_snap = json.loads((rundir / "pkg-status.json").read_text())
    except (OSError, ValueError):
        pkg_snap = {}
    # The HOST pmbootstrap dir is the wrong place to look for the lock on the
    # default path: a workspace build takes it in sandbox._sandbox_pmb(), so
    # probing ~/.local/var/pmbootstrap reported "buildroot free" for exactly
    # the foreign build this line exists to catch. `pkg` already decides this
    # the right way; reuse its decision rather than a second copy. Never
    # fatal: brief must not crash because a lock file is unreadable.
    import porthole_cmd_build as _build
    import porthole_cmd_pkg as _pkg

    try:
        pmb_workdir = _pkg._pmb_workdir(ctx, _build._workspace_usable(ctx)[0])
        holder = buildroot.lock_holder(pmb_workdir)
    except Exception:  # noqa: BLE001
        holder = ""
    builds_line = activity_summary(pkg_snap, holder)

    laws = _notes(root, "laws")
    profile_gaps = [k for k in ("PORTHOLE_SOC", "PORTHOLE_ARCH",
                                "PORTHOLE_REBOOT_BUDGET_S")
                    if not cfg.get(k)]

    _laws, _findings = _from_brain(pathlib.Path(ctx.root), device,
                                   cfg.get("PORTHOLE_SOC", ""))

    # ORDER IS LOAD-BEARING. state(max_age=30) writes the state cache, and the
    # milestone probes in _port_state() below READ it. Evaluate milestones
    # first and they see a stale reading or none -- which is how `brief` came
    # to print `state BOOTED` directly above `next` rows saying "device state
    # not probed (run porthole doctor)", advising the command that had already
    # answered. tests/test_milestones.py pins this.
    payload = {
        "porthole_version": version(root),
        "root": str(root),
        "device": {
            "codename": device,
            "name": cfg.get("PORTHOLE_DEVICE_NAME", ""),
            "soc": cfg.get("PORTHOLE_SOC", ""),
            "state": state,
            "kernel": kernel,
            # Redacted here and NOT in the human output below: this is the
            # half that gets pasted into a note or a PR. See
            # porthole_secrets.redact_login.
            "ssh_target": secrets.redact_login(cfg.get("PHONE", "")),
            "profile_gaps": profile_gaps,
            "traps": _device_traps(cfg),
        },
        "workspace": workspace,
        "hooks": hooks,
        "config_drift": drifts,
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
        "rules": [{"id": r.id, "level": r.level, "rule": r.statement,
                   "why": r.why, "enforced_by": list(r.enforced_by)}
                  for r in rules.RULES],
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
        "aports": _carried_aports(ctx, device),
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
                  "NOAUTH": "yellow", "ABSENT": "grey"}.get(state, "grey")
        ctx.out.kv("state", ctx.out.paint(state, colour), w)
        if payload["device"].get("kernel", {}).get("evidence"):
            ctx.out.kv("kernel", payload["device"]["kernel"]["evidence"], w)
        ctx.out.kv("tools", str(len(tools)), w)
        ctx.out.kv("builds", builds_line, w)
        carried = payload["aports"]
        if carried["required"]:
            ctx.out.kv("carries", f"{len(carried['required'])} aports "
                                  f"carried (porthole pkg drift)", w)
        if payload["device"]["traps"]:
            ctx.out.blank()
            ctx.out.heading("device traps encoded in the profile")
            for trap in payload["device"]["traps"]:
                ctx.out(f"  {ctx.out.sym('•', '-')} {trap}")
        ctx.out.blank()
        ctx.out.heading("rules that cost sessions when broken")
        for r in RULES:
            ctx.out(f"  {ctx.out.sym('•', '-')} {ctx.out.paint(r.statement, 'bold')}")
            ctx.out(f"    {ctx.out.paint(r.why, 'grey')}")
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
            m = port.get("matrix")
            if isinstance(m, dict) and m:
                # Defensive: a summary without "at" (an old cache, or one
                # `porthole matrix` hasn't written yet) must still render --
                # just without an age, never a crash in the first command an
                # agent runs.
                age = ""
                at = m.get("at")
                if isinstance(at, (int, float)):
                    import porthole_milestones as ms
                    age = ", probed {} ago".format(ms._ago(time.time() - at))
                ctx.out.kv("works", f"{m.get('works', 0)}/{m.get('total', 0)} "
                                    f"capabilities "
                                    f"({m.get('untested', 0)} untested{age})", w)
            for item in port.get("stale", [])[:3]:
                ctx.out.warn(f"{item['title']}: {item['detail']}")
            ctx.out.blank()

        if payload["config_drift"]:
            ctx.out.heading("the environment disagrees with the profile")
            for d in payload["config_drift"]:
                verb = "REFUSES a build" if d["blocking"] else "worth checking"
                ctx.out(ctx.out.paint(
                    f"  {d['key']}: environment says {d['winning']}, "
                    f"{d['committed_layer']} says {d['committed']}  "
                    f"({verb})", "yellow"))
            ctx.out.blank()

        if not payload["hooks"]["installed"]:
            ctx.out.heading("git hooks")
            ctx.out(ctx.out.paint("  " + payload["hooks"]["message"], "yellow"))
            ctx.out.blank()

        if not payload["workspace"]["ready"]:
            # stdout, not out.warn: warn goes to stderr, which reorders against
            # block-buffered stdout the moment this is piped -- and this is the
            # verb agents pipe. A warning that lands somewhere else in the
            # output is a warning that gets read as belonging to something else.
            ctx.out.heading("the build workspace")
            ctx.out(ctx.out.paint(
                "  " + payload["workspace"]["message"], "yellow"))
            ctx.out.blank()

        ctx.out.heading("suggested next steps")
        for step in payload["next"]:
            ctx.out.hint(step)

    return ctx.emit(payload, render)


def _write_run_json(ctx, name: str, blob) -> None:
    """Leave a blob in the run dir for the milestone probes. Best effort:
    `brief` must never fail because bookkeeping did."""
    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    try:
        rundir.mkdir(parents=True, exist_ok=True)
        tmp = rundir / (name + ".tmp")
        tmp.write_text(json.dumps(blob, indent=2))
        os.replace(tmp, rundir / name)
    except OSError:
        pass


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
                   "Use tools/ph-to-fastboot.sh.")
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


def _carried_aports(ctx, device):
    """What this port carries on top of stock, for the session brief.

    Cheap on purpose: reads the manifest and nothing else. Whether upstream
    has overtaken any of them is `porthole pkg drift`, which compares every
    carried aport against its upstream tree -- more work than a session's
    opening brief should do before anyone has asked for it.
    """
    import porthole_aports_manifest as man

    manifest = man.load(ctx.root, device)
    return {"required": man.names(manifest, "required"),
            "optional": man.names(manifest, "optional"),
            "problems": man.problems(manifest)}


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
        import porthole_milestones as ms
        _, summary, has_markers = nxt.collect(ctx)
        blob = ms._read_run_json(ctx, "matrix.json")
        matrix = blob.get("summary") if isinstance(blob, dict) else None
        matrix = dict(matrix) if isinstance(matrix, dict) else {}
        # Carry the cache's own timestamp through: a summary with no age
        # attached renders identically at 30 seconds and three weeks old,
        # which is the same claim-that-stands-forever bug the milestone
        # layer had. "at" is a raw timestamp (not pre-formatted) so a
        # machine consumer of `brief --json` can age it too, the same way
        # `render()` below does for a human.
        at = blob.get("at") if isinstance(blob, dict) else None
        if isinstance(at, (int, float)):
            matrix["at"] = at
        return {**summary, "checklist_has_markers": has_markers,
                "matrix": matrix}
    except Exception:  # noqa: BLE001
        return {}


def _from_brain(root: pathlib.Path, device: str, soc: str):
    """The laws and the findings, READ from brain/ rather than restated here.

    The laws were hand-written here once and stayed that way while brain/laws/
    grew to ten notes, so the brief showed three and silently omitted seven --
    including "read the vendor before inventing a mechanism", which cost a day.
    Writing a law had no effect on what any agent was shown. Read now, so it
    cannot drift again.

    RULES had the identical defect one level up and kept it longer: it was
    hand-written while AGENTS.md and the skill grew their own copies, and the
    three disagreed. lib/porthole_rules.py is the fix.

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
        steps.append("tools/ph-reboot.sh   # leave the bootloader, re-arming the slot")
    elif state == "INITRAMFS":
        steps.append("tools/tsh.py 'dmesg | grep pmOS-rd'   # why root did not mount")
    elif state == "NOAUTH":
        steps.append(f"ssh-copy-id -i ~/.porthole/device_key "
                     f"{cfg.get('PHONE', '')}   # userspace is up, the key "
                     f"is not installed")
    elif state == "FROZEN":
        steps.append("tools/ph-recover.sh   # kernel alive, userspace gone")
    elif state == "BOOTED":
        steps.append("porthole doctor   # confirm sudo -n works before anything else")
    if gaps:
        steps.append(f"fill profiles/{device}/device.env: {', '.join(gaps)}")
    steps.append("porthole brain search --severity law   # ten notes, read once")
    return steps


SPEC = {
    "verb": "brief",
    "order": 15,
    "group": "knowledge",
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
