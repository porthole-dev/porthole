#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole experiment` -- run something, and know what the device was before.

Most wrong conclusions on this port were not bad reasoning. They were
confounds: a leftover `aplay` still holding a backend, a sound server holding a
PCM, a wedged DSP from the previous run, a screen blanking setting nobody set
this session. Three of one day's findings were later refuted by exactly that
class of state (docs/RETRO-2026-08-26.md item 10).

A confound is invisible in the result. The measurement succeeds, the number is
real, and it is answering a question about a device you were not testing.

So: snapshot the device before, refuse if it is already contaminated, run the
thing, snapshot after, and diff. The pattern is lifted from `tools/tk-lab.py`,
whose snap/diff loop made audio experiments repeatable -- generalised here so it
is not one subsystem's private discipline.

**Probes are per-device, the verb is not.** What counts as contamination is a
device fact, so it lives in `profiles/<codename>/probes.conf` beside every other
device fact. Generic probes that apply anywhere are built in.
"""
from __future__ import annotations

import json
import pathlib
import shlex
import subprocess
import time

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_STATE, EX_USAGE

# Probes every device can answer, and which have explanatory power everywhere.
#
# boot_id is the one that matters most and is the cheapest: if it changes across
# an experiment the device rebooted underneath you, and every number either side
# of that describes a different running system.
GENERIC_PROBES = [
    ("boot_id", "cat /proc/sys/kernel/random/boot_id"),
    ("uptime", "cut -d' ' -f1 /proc/uptime"),
    ("modules", "lsmod | tail -n +2 | awk '{print $1}' | sort | tr '\\n' ' '"),
    ("failed_units", "systemctl --failed --no-legend --plain 2>/dev/null "
                     "| awk '{print $1}' | tr '\\n' ' '"),
    ("dmesg_tail", "dmesg 2>/dev/null | tail -5"),
]

# Confounds every device can check. Output means contaminated.
GENERIC_CONFOUNDS = [
    ("failed_units", "systemctl --failed --no-legend --plain 2>/dev/null "
                     "| awk '{print $1}' | tr '\\n' ' '"),
]


def _probe_file(ctx) -> pathlib.Path | None:
    device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    if not device:
        return None
    path = pathlib.Path(ctx.root) / "profiles" / device / "probes.conf"
    return path if path.is_file() else None


def load_probes(ctx) -> tuple[list, list, pathlib.Path | None]:
    """Generic probes, plus whatever the profile adds.

    Format, one per line:

        name: command            a probe -- recorded before and after
        !name: command           a CONFOUND -- any output means contaminated

    Deliberately not YAML. It is two fields, and a dependency to parse two
    fields is how a toolbox that runs on a broken host stops running.
    """
    probes = list(GENERIC_PROBES)
    confounds = list(GENERIC_CONFOUNDS)
    path = _probe_file(ctx)
    if not path:
        return probes, confounds, None
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, command = line.partition(":")
        if not sep or not command.strip():
            continue
        name = name.strip()
        if name.startswith("!"):
            confounds.append((name[1:].strip(), command.strip()))
        else:
            probes.append((name, command.strip()))
    return probes, confounds, path


def snapshot(dev, probes: list) -> dict:
    out = {}
    for name, command in probes:
        try:
            out[name] = (dev.run(command, timeout=15) or "").strip()
        except Exception as exc:  # noqa: BLE001 -- a probe must not kill the run
            out[name] = f"<probe failed: {type(exc).__name__}>"
    return out


def _diff(before: dict, after: dict) -> list[tuple[str, str, str]]:
    keys = sorted(set(before) | set(after))
    return [(k, before.get(k, ""), after.get(k, ""))
            for k in keys if before.get(k, "") != after.get(k, "")]


def _check_confounds(dev, confounds: list) -> list:
    """Run the confound checks. A function, not a loop in the caller.

    As a loop it was `for name, command in confounds`, which rebound the
    caller's `command` -- the user's argv -- so subprocess.run then tried to
    exec a probe string. Caught on the first live run. Keeping it in its own
    scope makes that class of mistake impossible rather than merely fixed.
    """
    dirty = []
    for name, probe in confounds:
        try:
            got = (dev.run(probe, timeout=15) or "").strip()
        except Exception:  # noqa: BLE001 -- a check must not kill the run
            continue
        if got:
            dirty.append((name, got))
    return dirty


def cmd_experiment(args, ctx) -> int:
    probes, confounds, path = load_probes(ctx)

    # `probes` is detected rather than declared as an argparse choice: a
    # positional with choices=[...] swallows the first word of the command, so
    # `experiment -- true` was rejected as an invalid action.
    command = list(args.command or [])
    if command[:1] == ["probes"] and len(command) == 1:
        def render():
            o = ctx.out
            o.heading(f"{len(probes)} probe(s), {len(confounds)} confound check(s)")
            o.kv("profile", str(path) if path else
                 o.paint("no probes.conf -- generic only", "grey"), 10)
            o.blank()
            for pname, pcmd in probes:
                o(f"  {o.paint(pname.ljust(16), 'cyan')} {o.paint(pcmd, 'grey')}")
            if confounds:
                o.blank()
                o.heading("confounds -- any output means the device is dirty")
                for cname, ccmd in confounds:
                    o(f"  {o.paint(cname.ljust(16), 'yellow')} "
                      f"{o.paint(ccmd, 'grey')}")
            if not path:
                o.blank()
                o.hint(f"profiles/{ctx.cfg.get('PORTHOLE_DEVICE','<device>')}"
                       f"/probes.conf", "to add device-specific ones")
        return ctx.emit({"probes": [{"name": n, "command": c} for n, c in probes],
                         "confounds": [{"name": n, "command": c}
                                       for n, c in confounds],
                         "profile": str(path) if path else None}, render)

    if not command:
        raise Bail("nothing to run", EX_USAGE,
                   "porthole experiment -- <command>    (or: experiment probes)")

    state = ctx.device().state()
    if state != "BOOTED":
        raise Bail(f"the device is {state}, not BOOTED", EX_STATE,
                   "an experiment needs a device that can answer probes")

    dev = ctx.device()

    # Contamination is checked BEFORE the run, because afterwards you cannot
    # tell it from the result.
    dirty = _check_confounds(dev, confounds)

    if dirty and not args.allow_dirty:
        lines = "; ".join(f"{n}: {v}" for n, v in dirty)
        raise Bail(f"the device is already contaminated -- {len(dirty)} "
                   f"confound(s) tripped", EX_STATE,
                   f"{lines}\n"
                   "  Clear it and re-run, or pass --allow-dirty if this state "
                   "IS the experiment.\n"
                   "  A result measured on top of this describes a device you "
                   "did not mean to test.")
    if dirty:
        ctx.out.warn(f"{len(dirty)} confound(s) present, continuing because "
                     f"--allow-dirty: " + "; ".join(n for n, _ in dirty))

    before = snapshot(dev, probes)
    started = time.time()
    proc = subprocess.run(command)
    elapsed = time.time() - started
    after = snapshot(dev, probes)

    changed = _diff(before, after)
    rebooted = before.get("boot_id", "") != after.get("boot_id", "")

    run_dir = pathlib.Path(ctx.cfg.get("PORTHOLE_RUNDIR", "") or
                           pathlib.Path(ctx.root) / ".run") / "experiments"
    tag = args.tag or time.strftime("%Y%m%d-%H%M%S")
    record = {"tag": tag, "command": command, "rc": proc.returncode,
              "seconds": round(elapsed, 2), "rebooted": rebooted,
              "dirty_before": [{"confound": n, "value": v} for n, v in dirty],
              "before": before, "after": after,
              "changed": [{"probe": k, "before": b, "after": a}
                          for k, b, a in changed]}
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f"{tag}.json").write_text(json.dumps(record, indent=2))
        record["saved"] = str(run_dir / f"{tag}.json")
    except OSError as exc:
        ctx.out.warn(f"could not save the record: {exc}")

    def render():
        o = ctx.out
        o.blank()
        o.heading(f"experiment {tag} — rc {proc.returncode} in {elapsed:.1f}s")
        if rebooted:
            o.blank()
            o("  " + o.paint("THE DEVICE REBOOTED DURING THIS RUN.", "red"))
            o("  " + o.paint("boot_id changed, so the before and after "
                             "describe different running systems.", "red"))
            o("  " + o.paint("Nothing below is a comparison. Re-run it.", "red"))
        o.blank()
        if not changed:
            o("  nothing the probes can see changed.")
            o(o.paint("  That is a result only if a probe could have seen the "
                      "effect —\n  otherwise it is an experiment that did not "
                      "run. See brain/laws/.", "grey"))
        else:
            o.heading(f"{len(changed)} probe(s) changed")
            for name, b, a in changed:
                o(f"  {o.paint(name, 'cyan')}")
                o(f"    before  {o.paint(b or '(empty)', 'grey')}")
                o(f"    after   {a or '(empty)'}")
        if record.get("saved"):
            o.blank()
            o.kv("saved", record["saved"], 8)
    ctx.emit(record, render)
    return EX_OK if proc.returncode == 0 else EX_FAIL


SPEC = {
    "verb": "experiment",
    "order": 45,
    "group": "device",
    "help": "run something with the device state captured either side",
    "description": (
        "Most wrong conclusions on a bring-up are confounds, not bad logic: a\n"
        "leftover process holding a device, a sound server holding a PCM, a\n"
        "wedged DSP from the previous run. The measurement succeeds and answers\n"
        "a question about a device you were not testing.\n\n"
        "This snapshots the device before, REFUSES if it is already dirty, runs\n"
        "the command, snapshots after, and diffs. Probes and confound checks\n"
        "come from profiles/<codename>/probes.conf, because what counts as\n"
        "contamination is a device fact."),
    # NOT escapes_scope: that flag marks verbs which WRITE outside their own
    # profile -- build into the chroot, flash onto the device, aports into
    # pmaports -- and it requires --yes. This writes only gitignored run state
    # and executes a command the caller typed, which is what `porthole run`
    # does, and run does not set it either.
    "args": [
        (["--tag"], {"metavar": "NAME", "help": "name this run's record"}),
        (["--allow-dirty"], {"action": "store_true",
                             "help": "run even though a confound tripped"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
        (["command"], {"nargs": "*", "metavar": "CMD",
                       "help": "`probes` to list what is captured, or the "
                               "command to run, after --"}),
    ],
    "run": cmd_experiment,
    "examples": [
        "porthole experiment probes",
        "porthole experiment --tag mic-gain tools/tk-capture.sh 20",
        "porthole experiment tools/tk-suspend-cycle.sh 5",
    ],
}
