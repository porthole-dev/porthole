#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole matrix` -- what works on this phone, and what proves it.

Availability and function are different questions, and collapsing them is
what made "wifi ✓" true for four sessions on a phone that would not associate
to the one network that mattered. So both are asked, both are reported, and
neither is derived from the other.

Read-only by construction: every probe answers from state that already
exists. There is deliberately no --deep tier -- a verb that can leave the
phone somewhere you did not find it is not one you can run whenever, and
"whenever" is the whole point of a matrix.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import porthole_capabilities as caps
from porthole_cli import Bail, EX_OK, EX_STATE


def _profile_conf(ctx) -> str:
    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    if not device:
        return ""
    path = pathlib.Path(ctx.root) / "profiles" / device / "capabilities.conf"
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def cmd_matrix(args, ctx) -> int:
    merged = caps.merge(caps.GENERIC, caps.parse(_profile_conf(ctx)))

    device = ctx.device()
    state = device.state(max_age=30)
    if state != "BOOTED":
        # 76, not 1: the device is in the wrong state, and waiting will not
        # fix it. A matrix of a phone that is not answering is not a matrix.
        raise Bail(f"the device is {state}, not BOOTED", EX_STATE,
                   "a capability matrix is read FROM the running system; "
                   "boot it first")

    text = device.run(caps.script(merged), timeout=args.timeout) or ""
    results = caps.demux(text)
    table = rows(merged, results)
    summary = summarise(table)
    blob = {"at": time.time(), "device": ctx.cfg.get("PORTHOLE_DEVICE", ""),
            "capabilities": table, "summary": summary}

    rundir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR") or (ctx.root / ".run"))
    try:
        rundir.mkdir(parents=True, exist_ok=True)
        # tmp + os.replace, matching brief's _write_run_json: Task 26's
        # milestone probes read this file, and a kill or a full disk
        # mid-write must never leave them a truncated matrix.json -- a half
        # a file is worse than no file, which correctly reads as "nobody
        # has looked yet".
        tmp = rundir / "matrix.json.tmp"
        tmp.write_text(json.dumps(blob, indent=2))
        os.replace(tmp, rundir / "matrix.json")
    except OSError:
        pass          # a matrix that could not be cached is still a matrix

    def render():
        o = ctx.out
        o.heading("{}  {}/{} working, {} untested".format(
            blob["device"], summary["works"], summary["total"],
            summary["untested"]))
        o.blank()
        o("  {:<16}{:<9}{:<7}{}".format("capability", "present", "works",
                                        "evidence"))
        mark = {"yes": "green", "no": "red", "?": "grey"}
        for row in table:
            o("  {:<16}{:<9}{:<7}{}".format(
                row["name"],
                o.paint(row["present"], mark[row["present"]]),
                o.paint(row["works"], mark[row["works"]]),
                o.paint(row["evidence"][:44], "grey")))
        o.blank()
        o(o.paint("  `?` means no probe ran -- it is not a pass and not a "
                  "failure", "grey"))
        o(o.paint("  porthole matrix --json   every cell with its command "
                  "and exit status", "cyan"))

    ctx.emit(blob, render)
    return EX_OK


def rows(merged, results) -> list:
    """One row per capability, with both verdicts and both commands.

    Availability and function are SEPARATE columns and neither is derived from
    the other. taimen wifi was present for four sessions while refusing to
    associate to the one network that mattered; a single column would have
    said "wifi ✓" for all four.
    """
    out = []
    for name, fields in merged:
        row = {"name": name}
        for field in caps.FIELDS:
            record = results.get((name, field))
            row[field] = caps.verdict(record if fields.get(field) else None)
            row[field + "_cmd"] = fields.get(field, "")
            row[field + "_rc"] = (record or {}).get("rc")
        # The evidence a human can re-run and an agent cannot invent. Prefer
        # what `works` printed: that is the interesting half.
        row["evidence"] = ((results.get((name, "works")) or {}).get("out")
                           or (results.get((name, "present")) or {}).get("out")
                           or "")
        if not row["works_cmd"]:
            row["evidence"] = row["evidence"] or "no works: probe defined"
        out.append(row)
    return out


def summarise(rows) -> dict:
    """Counts, with `?` counted as untested and NEVER as working.

    The rule the whole verb rests on. A summary that folds unknowns into
    either column is a summary that lies in one direction or the other, and
    "further along than you are" is the direction that costs sessions.
    """
    return {"total": len(rows),
            "works": sum(1 for r in rows if r["works"] == "yes"),
            "present": sum(1 for r in rows if r["present"] == "yes"),
            "untested": sum(1 for r in rows if r["works"] == "?")}


SPEC = {
    "verb": "matrix",
    "order": 18,
    "help": "what works on this device, tested separately from what exists",
    "description": (
        "Availability and function are different questions and this asks\n"
        "both. A capability that is present and does not work reads as\n"
        "present-and-not-working, never as a tick.\n\n"
        "Every cell prints the command that produced it, so a human can\n"
        "re-run it and an agent cannot invent it. Where no probe exists the\n"
        "cell is `?` -- which is not partial credit and never advances a\n"
        "milestone.\n\n"
        "Read-only and non-disruptive: every probe answers from state that\n"
        "already exists and induces nothing, so this is safe to run at any\n"
        "time. It never suspends the device or re-associates a radio."),
    "args": [
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
        (["--timeout"], {"type": int, "default": 90, "metavar": "SEC",
                         "help": "seconds for the probe run (default 90)"}),
    ],
    "run": cmd_matrix,
    "examples": ["porthole matrix", "porthole matrix --json"],
}
