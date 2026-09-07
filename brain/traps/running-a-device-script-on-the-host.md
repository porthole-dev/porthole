---
id: running-a-device-script-on-the-host
title: A device-side script run on the host produces plausible, entirely wrong output
scope: generic
subsystem: method
severity: trap
confidence: proven
evidence: porthole `porthole run`, 2026-08-23; tests/test_cli.py
first-learned: 2026-08-23
---

Some tools execute **on** the device: pushed with `scp`, piped through
`ssh ... sh -s`, or installed as a unit. Run one on your workstation by mistake
and it does not error. It reads `/proc/loadavg`, `/proc/meminfo` and `ps` — all
of which exist on both machines — and prints a perfectly well-formed result
about the wrong computer.

**The worked example.** `porthole run ph-sysstate.sh` executed locally and
printed `memavail=22481444kB procs=457 top=[firefox]`. That is a workstation.
The phone has 2.5 GB and no firefox. Nothing in the output said so; the numbers
were internally consistent and the format was exactly right.

The tell, when there is one, is a device-only field: `ftm4_irq=0` came back
empty on the host because that sysfs node does not exist there. **An empty field
in an otherwise healthy-looking line is worth more attention than a missing
one** — see [[every-test-needs-a-positive-control]].

Two defences:

1. **Declare it.** Every porthole tool's header says `needs: on-device` when it
   runs there, and `porthole run` pipes such a tool to the device rather than
   executing it locally — or refuses with exit 76 if the device is not reachable.
2. **Put a device-only value in the output.** A field that can only exist on the
   target is a free positive control for "am I even on the right machine".

Generalises past phones: the same trap catches anyone running a container's
health script on the host, or a production diagnostic against staging.

Related: [[every-test-needs-a-positive-control]],
[[instrument-guilty-until-proven-innocent]].
