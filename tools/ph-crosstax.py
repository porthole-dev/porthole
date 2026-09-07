#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, samples the workspace container)
# env: PORTHOLE_SANDBOX_CONTAINER
# exits: 0 measured · 1 nothing was building · 69 no container to sample
"""ph-crosstax.py [SECONDS] -- how much of a cross build runs under qemu.

crossdirect routes every COMPILE to the native cross compiler and every LINK
to the target-arch binary under qemu, on purpose
(brain/findings/crossdirect-hands-the-linker-to-qemu-on-purpose.md). That is
the mechanism. The PROPORTION is per-package and has only ever been measured
once, by hand, against a package nobody is waiting on -- and quoted about
webkit ever since.

Sampling `ps` rather than reading CPU time per process: the processes are
short-lived -- a compile is seconds -- so anything that opens /proc per pid
misses most of them. A count of what is running, often, is what the original
finding used and is what this reproduces.

    ph-crosstax.py 300            # five minutes of whatever is building
    ph-crosstax.py 300 --json
    ph-crosstax.py 600 --interval 2

What the numbers mean. `share` is a share of PROCESS-SAMPLES, not of CPU time
and not of wall clock: of everything this saw running on either side, what
fraction was on each. A qemu process and a native one are not the same amount
of work, so this ranks where the parallelism goes, not where the seconds go.
Said plainly because the finding it replaces was read as though it were CPU
time.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

CONTAINER = os.environ.get("PORTHOLE_SANDBOX_CONTAINER", "porthole-sandbox")

EX_OK, EX_FAIL, EX_UNAVAILABLE = 0, 1, 69

# A link, re-executed as the target-arch binary and handed to qemu by binfmt.
# Anchored at the start: `qemu-aarch64-static` appearing later on a line is an
# argument to something else, most often a `ps` or a grep looking for it.
_QEMU = re.compile(r"^(?:\S*/)?qemu-[a-z0-9_]+-static\b")
# A compile, sent to the native cross compiler with ccache in front of it.
# The ccache path is the discriminator, not the compiler name: the same
# `clang++` name appears on both sides, and only the path says which is which.
_NATIVE = re.compile(r"^\S*/ccache/bin/\S+")
# `ps` columns: pid, ppid, state, then the command with its arguments.
_PS_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$")


def classify(line: str) -> str:
    """`qemu`, `native`, or `""` for one command line. PURE.

    Everything else is deliberately neither. ninja, sh, abuild and the
    sampler's own `ps` are real processes doing real work, and counting them
    on either side makes the split a ratio of noise.
    """
    line = (line or "").strip()
    if _QEMU.match(line):
        return "qemu"
    if _NATIVE.match(line):
        return "native"
    return ""


def tally_sample(ps_output: str) -> dict:
    """`{side: count}` for one `ps` snapshot. PURE.

    A side that ran nothing contributes no key rather than a zero: "no
    compiles in this window" and "some, all of them native" are different
    answers, and a zero makes them look alike.

    Zombies are skipped. The original finding caught 40 `[cc]` zombies in one
    snapshot -- counting them as running work invents a compile phase that had
    already exited.
    """
    tally = {}
    for row in (ps_output or "").splitlines():
        match = _PS_ROW.match(row)
        if not match:
            continue
        _pid, _ppid, state, args = match.groups()
        if state.startswith("Z"):
            continue
        side = classify(args)
        if side:
            tally[side] = tally.get(side, 0) + 1
    return tally


def snapshot(container: str):
    """One `ps` from inside the workspace, or None when it cannot be asked."""
    try:
        done = subprocess.run(
            ["podman", "exec", container, "ps", "-eo", "pid,ppid,stat,args="],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="how much of a cross build runs under qemu")
    ap.add_argument("seconds", nargs="?", type=float, default=60.0,
                    help="length of the sampling window (default 60)")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="seconds between samples (default 5)")
    ap.add_argument("--container", default=CONTAINER)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    args = ap.parse_args(argv)

    if snapshot(args.container) is None:
        print("ph-crosstax: cannot run `ps` in {} -- is the workspace up?"
              .format(args.container), file=sys.stderr)
        print("  -> porthole sandbox up", file=sys.stderr)
        return EX_UNAVAILABLE

    totals, by_binary, samples = {}, {}, 0
    started = time.monotonic()
    deadline = started + args.seconds
    while True:
        out = snapshot(args.container)
        if out is None:
            break
        samples += 1
        for row in out.splitlines():
            match = _PS_ROW.match(row)
            if not match:
                continue
            _pid, _ppid, state, cmd = match.groups()
            if state.startswith("Z"):
                continue
            side = classify(cmd)
            if not side:
                continue
            totals[side] = totals.get(side, 0) + 1
            name = os.path.basename(cmd.split()[0])
            by_binary.setdefault(side, {})
            by_binary[side][name] = by_binary[side].get(name, 0) + 1
        now = time.monotonic()
        if now >= deadline:
            break
        # A SAMPLE RATE, not a wait: this is how often the window is observed,
        # and there is no condition to poll for -- the thing being measured is
        # what is running at each tick. `porthole tools audit` reads a bare
        # sleep as the poll-never-sleep defect, and it is right to everywhere
        # else in this repo.
        time.sleep(min(args.interval, deadline - now))

    window = time.monotonic() - started
    seen = sum(totals.values())
    payload = {
        "samples": samples,
        "window_s": round(window, 1),
        "native": {"procs": totals.get("native", 0),
                   "share": (totals.get("native", 0) / seen) if seen else None},
        "qemu": {"procs": totals.get("qemu", 0),
                 "share": (totals.get("qemu", 0) / seen) if seen else None},
        "by_binary": by_binary,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("samples {}  window {:.0f}s  container {}".format(
            samples, window, args.container))
        if not seen:
            print("  nothing was compiling -- no native or qemu process was "
                  "seen in any sample")
        for side in ("native", "qemu"):
            row = payload[side]
            share = "--" if row["share"] is None else "{:.0%}".format(row["share"])
            print("  {:<7} {:>6} process-samples  {:>4}  mean {:.1f} per sample"
                  .format(side, row["procs"], share,
                          row["procs"] / samples if samples else 0.0))
            for name, count in sorted(by_binary.get(side, {}).items(),
                                      key=lambda kv: -kv[1])[:5]:
                print("            {:<28} {}".format(name, count))
    # Exit 1 for "nothing was building": that is a real answer about the
    # window, and reporting it as success would let an empty measurement be
    # quoted as a split of 0%.
    return EX_OK if seen else EX_FAIL


if __name__ == "__main__":
    sys.exit(main())
