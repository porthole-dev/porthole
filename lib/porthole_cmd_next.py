#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole next` -- where am I in this port, and what is the one next thing.

The question a porter asks all day. Before this the toolkit could not answer it:
`new-device` wrote a 26-item checklist and nothing ever read it again.

Two rules shape the output:

**One answer, not a dashboard.** A wall of ticks buries the single line that
matters. Done items are counted; what is reported is the next action, what is
blocking, and anything claiming to be done that provably is not.

**A probe outranks a tick.** Where the tool can check something, a checkbox is a
second opinion -- and where they disagree it is reported as `stale` rather than
believed. A progress display that trusts a stale tick is worse than no display,
because it is confidently wrong in the direction of "you are further along than
you are".
"""
from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys

import porthole_milestones as ms
from porthole_cli import Bail, EX_FAIL, EX_OK

MAX_LISTED = 6
WIDTH = 44


def _fit(text: str, width: int = WIDTH) -> str:
    """Trim on a word boundary. Cutting mid-word looks like a rendering bug."""
    if len(text) <= width:
        return text.ljust(width)
    cut = text[:width - 1].rsplit(" ", 1)[0]
    return (cut + "…").ljust(width)


def _checklist(ctx) -> pathlib.Path:
    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    if not device:
        raise Bail("no device selected", EX_FAIL,
                   "porthole use <codename>, or porthole new-device <codename>")
    return pathlib.Path(ctx.root) / "profiles" / device / "checklist.md"


def cmd_regenerate(args, ctx) -> int:
    """Rebuild checklist.md from the milestone table, keeping ticks.

    Opt-in and never automatic. Rewriting a committed file as a side effect of
    a read-looking verb is the kind of surprise that costs a tool its
    trustworthiness -- so `next` reports that a checklist is out of date and
    leaves the decision here.
    """
    path = _checklist(ctx)
    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    unmatched: list[str] = []
    ticks: dict[str, bool] = {}

    if path.exists():
        text = path.read_text(errors="replace")
        ticks = ms.read_ticks(path)
        if not ticks:
            # No markers: a checklist from before this convention. Carry the
            # ticks across by text, and REPORT what could not be matched --
            # silently losing one would make the migration untrustworthy.
            ticks, unmatched = ms.match_legacy_ticks(text)

    if not args.yes:
        def render():
            o = ctx.out
            o.heading(f"would rewrite {path.relative_to(ctx.root)}")
            o(f"  {len(ms.MILESTONES)} milestones, {sum(ticks.values())} tick(s) "
              f"carried over")
            for item in unmatched:
                o.warn(f"could not match a ticked item: {item}")
            o.blank()
            o.hint("porthole next --regenerate --yes")
        return ctx.emit({"path": str(path), "carried": sorted(ticks),
                         "unmatched": unmatched, "written": False}, render)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ms.render_checklist(device, ticks))

    def render():
        o = ctx.out
        o(f"{o.paint(o.sym('✓', 'ok'), 'green')} wrote "
          f"{path.relative_to(ctx.root)}")
        o(f"  {len(ms.MILESTONES)} milestones, {sum(ticks.values())} tick(s) "
          f"carried over")
        for item in unmatched:
            o.warn(f"could not match a ticked item: {item}")
        o.blank()
        o.hint("porthole next")

    return ctx.emit({"path": str(path), "carried": sorted(ticks),
                     "unmatched": unmatched, "written": True}, render)


def collect(ctx) -> tuple[list[dict], dict, bool]:
    """Milestone rows, the summary, and whether the checklist has markers."""
    try:
        path = _checklist(ctx)
    except Bail:
        raise
    ticks = ms.read_ticks(path) if path.exists() else {}
    has_markers = bool(ticks) or not path.exists()
    if path.exists() and not ticks:
        # Distinguish "no markers" from "markers, none ticked": only the former
        # needs regenerating, and telling everyone to regenerate would be noise.
        text = path.read_text(errors="replace")
        has_markers = not ms.ITEM.search(text) or bool(ms.MARKER.search(text))
    rows = ms.evaluate(ctx, ticks)
    return rows, ms.summarise(rows), has_markers


def cmd_next(args, ctx) -> int:
    if args.regenerate:
        return cmd_regenerate(args, ctx)

    rows, summary, has_markers = collect(ctx)
    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    payload = {"device": device, **summary,
               "checklist_has_markers": has_markers,
               "milestones": [{k: r[k] for k in
                               ("id", "phase", "title", "state", "source",
                                "evidence")} for r in rows]}

    def render():
        o = ctx.out
        p = summary["progress"]
        bar = _bar(p["done"], p["total"], o)
        o.heading(f"{device}  {bar}  {p['done']}/{p['total']}"
                  + (f" · {p['phase']} ({p['phase_index']}/{p['phases']})"
                     if p["phase"] else ""))
        o.blank()

        # Only what needs attention. Everything else is in the count.
        shown = 0
        for row in rows:
            if row["source"] == "stale":
                o(f"  {o.paint('! stale  ', 'red')} {_fit(row['title'])} "
                  f"{o.paint(row['evidence'] or 'ticked, but not true', 'grey')}")
                shown += 1
        for row in rows:
            if row["state"] == ms.BLOCKED and shown < MAX_LISTED:
                o(f"  {o.paint('· blocked', 'yellow')} {_fit(row['title'])} "
                  f"{o.paint(row['evidence'], 'grey')}")
                shown += 1
        recent = [r for r in rows if r["state"] == ms.DONE][-3:]
        for row in recent:
            tag = "derived" if row["source"] == "derived" else "manual "
            o(f"  {o.paint('✓ ' + tag, 'green')} {_fit(row['title'])} "
              f"{o.paint(row['evidence'] or 'ticked', 'grey')}")
        if shown or recent:
            o.blank()

        nxt = summary["next"]
        if not nxt:
            o(o.paint("  Every milestone is done or blocked. That is either a "
                      "finished port\n  or a checklist that needs a new "
                      "milestone.", "green"))
            return
        o(f"{o.paint('next', 'bold')}  {o.paint(nxt['title'], 'cyan')}")
        if nxt["why"]:
            o(f" {o.paint('why', 'grey')}  {nxt['why']}")
        if nxt["command"]:
            o(f" {o.paint('how', 'grey')}  {nxt['command']}")
        if nxt["playbook"]:
            o(f" {o.paint('read', 'grey')} {nxt['playbook']}")
        if not has_markers:
            o.blank()
            o.warn("this checklist predates the milestone table — ticks are "
                   "not being read")
            o.hint("porthole next --regenerate")

    rc = ctx.emit(payload, render)
    if getattr(args, "json", False):
        return rc

    _maybe_offer(args, ctx, summary["next"])
    return EX_OK


def _bar(done_n: int, total: int, out) -> str:
    """Eight cells. Enough to see movement, too few to become a dashboard."""
    if not total:
        return ""
    filled = round(8 * done_n / total)
    return out.paint(out.sym("█", "#") * filled, "green") + \
        out.paint(out.sym("░", ".") * (8 - filled), "grey")


def _maybe_offer(args, ctx, nxt) -> None:
    """Offer to run the suggested command -- only when it is safe.

    `safe` means read-only or trivially reversible ON THE HOST. Anything that
    flashes, writes to the device, or changes a slot is printed and never
    offered, which is the same line `porthole sandbox` draws by printing
    privileged commands instead of running them.

    Non-TTY returns immediately: an agent must get data, never a prompt.
    """
    if not nxt or not nxt["command"] or not nxt["safe"]:
        return
    if args.run:
        pass                                   # explicitly asked for
    elif not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    else:
        ctx.out.blank()
        try:
            answer = input(f"  run `{nxt['command']}` now? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ctx.out.blank()
            return
        if answer not in ("y", "yes"):
            return

    # Only our own verbs are ever run. A milestone whose `how` is prose ("write
    # verify.sh") or another program is not something to hand to a shell.
    parts = shlex.split(nxt["command"])
    if not parts or parts[0] != "porthole" or any(
            c in nxt["command"] for c in "|><&$`"):
        ctx.out.warn("that step is a description, not a command to run")
        return
    ctx.out.blank()
    subprocess.run([sys.executable, str(pathlib.Path(ctx.root) / "bin" / "porthole"),
                    *parts[1:]])


SPEC = {
    "verb": "next",
    "order": 14,
    "group": "device",
    "help": "where am I in this port, and what is the one next thing",
    "description": (
        "Derives the port's state from what is actually on disk -- the profile,\n"
        "the working repo, pmaports, the brain -- and names the single next\n"
        "action, why it matters and the command for it.\n\n"
        "A probe always outranks a checklist tick. Where they disagree it is\n"
        "reported as stale rather than believed: being told you are further\n"
        "along than you are is worse than being told nothing.\n\n"
        "At a terminal it offers to run the next step when that step is safe.\n"
        "It never offers anything that flashes or touches the device."),
    "args": [
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
        (["--regenerate"], {"action": "store_true",
                            "help": "rebuild checklist.md from the milestone "
                                    "table, keeping ticks"}),
        (["--run"], {"action": "store_true",
                     "help": "run the next step without asking (safe steps only)"}),
        (["--yes"], {"action": "store_true",
                     "help": "regenerate: actually write the file"}),
    ],
    "run": cmd_next,
    "examples": [
        "porthole next",
        "porthole next --json",
        "porthole next --regenerate --yes",
    ],
}
