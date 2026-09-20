#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: PHONE, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST
# exits: 0 ok · 64 usage · 70 the phone did not answer
"""What is waking the CPU while nobody is using the phone?

Two /proc/interrupts snapshots a fixed wall-clock apart, differenced into a
per-IRQ rate. This is the instrument that found the ADC-TM comparator storm on
taimen on 2026-09-20: 1572 interrupts a second from one latched status bit,
about a whole CPU of an idle phone, invisible to every other measurement
anybody had taken.

    ph-irq-floor.py                 # 30 s window, anything above 1/s
    ph-irq-floor.py --seconds 60 --floor 0.2
    ph-irq-floor.py --json

WHAT MAKES THIS A MEASUREMENT AND NOT A NUMBER

  * Both snapshots are taken IN ONE ssh session, with the sleep between them
    on the phone. Two separate sessions put sshd's own wakeups inside the
    window and add a login to each end of it.
  * The elapsed time is read from the phone's own CLOCK_MONOTONIC either side,
    not from the host's idea of how long it waited. A 30 s window that
    actually ran 34 s reports rates 13 % high.
  * Rates are per second across ALL CPUs, summed, because an interrupt that
    migrates between cores is still one interrupt.

Read it with the screen OFF and nothing running: this is the floor, and
anything above single digits per second on an idle phone is worth a session.
The tool does not blank the screen for you -- doing that from here would make
the measurement depend on a display stack that is not what is being measured.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys

EX_USAGE = 64
EX_UNREACH = 70

# One command, so the window is bounded on the phone and not by ssh round trips.
REMOTE = r"""
read -r a b < /proc/uptime; echo "T0 $a"
cat /proc/interrupts
sleep %d
read -r a b < /proc/uptime; echo "T1 $a"
cat /proc/interrupts
"""


def parse(lines):
    """{name: total count} from one /proc/interrupts block.

    The header line names the CPUs, and that is the only reliable way to know
    how many count columns a row has.
    """
    out = {}
    ncpus = 0
    for line in lines:
        if not ncpus:
            head = line.split()
            if head and all(c.startswith("CPU") for c in head):
                ncpus = len(head)
            continue
        if ":" not in line:
            continue
        label, rest = line.split(":", 1)
        label = label.strip()
        fields = rest.split()
        # EXACTLY one column per CPU, taken from the header. Stopping at the
        # first non-numeric field instead looks right and is not: the trailing
        # description of a GPIO interrupt carries its pin number, and
        # "msmgpio 125 Level ftm4" then contributes 125 phantom interrupts to
        # a line whose real count is zero. That misread a dead touch
        # controller as a busy one on 2026-09-20.
        counts = fields[:ncpus]
        if len(counts) < ncpus or not all(c.isdigit() for c in counts):
            continue
        name = " ".join(fields[ncpus:])
        out[f"{label} {name}".strip()] = sum(int(c) for c in counts)
    return out


def main():
    ap = argparse.ArgumentParser(description="per-IRQ wake rate over an idle window")
    ap.add_argument("--seconds", type=int, default=30, help="window length (default 30)")
    ap.add_argument("--floor", type=float, default=1.0,
                    help="hide anything below this many per second (default 1.0)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.seconds < 5:
        print("a window under 5 s measures the ssh session, not the phone",
              file=sys.stderr)
        return EX_USAGE

    root = os.environ.get("PORTHOLE_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    # ph-lib.sh owns the ssh options and the target; going around it is how
    # tools end up working on exactly one desk.
    # shlex.quote, NOT subprocess.list2cmdline: that one quotes for the Windows
    # command line and leaves a newline-bearing script unquoted, so sh split it
    # into words and the first line of output was never "T0 <uptime>".
    script = (f'. "{root}/tools/ph-lib.sh"; '
              f'TK_RUN_TIMEOUT={args.seconds + 60} tk_run '
              + shlex.quote(REMOTE % args.seconds))
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    if proc.returncode != 0 or "T1 " not in proc.stdout:
        print(proc.stderr.strip() or "the phone did not answer", file=sys.stderr)
        return EX_UNREACH

    head, _, tail = proc.stdout.partition("T1 ")
    first = head.split("\n", 1)[0].split()
    if len(first) < 2 or first[0] != "T0":
        print("the phone did not answer with a clock -- got: "
              + head[:200].strip(), file=sys.stderr)
        return EX_UNREACH
    t0 = float(first[1])
    t1 = float(tail.split("\n", 1)[0])
    before = parse(head.split("\n")[1:])
    after = parse(tail.split("\n")[1:])
    elapsed = t1 - t0

    rows = []
    for name, end in after.items():
        delta = end - before.get(name, 0)
        if delta <= 0:
            continue
        rate = delta / elapsed
        if rate < args.floor:
            continue
        rows.append({"irq": name, "count": delta, "per_sec": round(rate, 2)})
    rows.sort(key=lambda r: -r["per_sec"])
    total = sum(max(0, end - before.get(n, 0)) for n, end in after.items())

    if args.json:
        json.dump({"seconds": round(elapsed, 2), "total_per_sec":
                   round(total / elapsed, 2), "irqs": rows}, sys.stdout, indent=2)
        print()
        return 0

    print(f"window {elapsed:.1f}s   total {total / elapsed:.1f}/s across all IRQs")
    print(f"{'per sec':>9}  {'count':>8}  irq")
    for r in rows:
        print(f"{r['per_sec']:>9.2f}  {r['count']:>8}  {r['irq']}")
    if not rows:
        print(f"  (nothing above {args.floor}/s -- this floor is clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
