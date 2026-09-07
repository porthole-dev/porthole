---
id: uprobes-do-not-attach-to-an-already-mapped-library
title: A uprobe set after the process started never fires
scope: generic
subsystem: perf
severity: trap
confidence: proven
evidence: "taimen 2026-09-05, kernel 7.2.2. Same probe (ThreadedCompositor::renderLayerTree, libwebkitgtk 2.52.6-r56 at file offset 0x226f588), same scroll workload, two orderings: armed against a running Epiphany, 0 events in the ftrace buffer; armed first and Epiphany launched after, 126 events in 35 s."
first-learned: 2026-09-05
---

**Symptom** — the probe is accepted, `uprobe_events` lists it, the event
directory exists, `perf record` writes a perf.data of a plausible size, and
there is not one single hit. `perf report` says "data has no samples";
`tracing/trace` has only its header. Every check you can think of says the
probe is fine, because it is: the function is running, in a process that
cannot see it.

**Cause** — the process already had the library mapped when the probe was
registered. The probe attaches to the inode, and the mapping it needs to patch
is the one made after. Nothing in the API tells you this: registration succeeds
because the inode is valid, and an empty result is indistinguishable from a
function that is never called -- which is exactly the wrong conclusion, and the
one you will draw.

**What to do** — arm the probes, THEN start the process. For a browser that
means stopping it, writing `uprobe_events`, enabling the events, and only then
launching:

    for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do
        systemctl --user stop "$u"; done
    printf 'p:wk/f %s:%s\nr:wk/f_ret %s:%s\n' "$LIB" "$OFF" "$LIB" "$OFF" > /sys/kernel/tracing/uprobe_events
    echo 1 > /sys/kernel/tracing/events/wk/enable
    systemd-run --user --scope ... epiphany URL &

**Take the positive control before you believe a null.** Probe something you
know runs every frame, in the same run. A null from a probe that could not have
fired is not a measurement --
[[every-test-needs-a-positive-control]].

**And on this kernel, read `tracing/trace`, not perf.** With the probes armed
before launch, ftrace's buffer filled while `perf record -a -e wk:<name>` on
the same probes still captured nothing. Whatever that is, ftrace is the path
that works; `tools/ph-wkphase.sh` uses it for that reason.

This retroactively puts a question mark over any measurement taken by arming
probes against an already-running browser -- `tools/ph-webframe.sh` and
`tools/ph-webdraws.sh` are both written that way. Related:
[[the-instrument-is-guilty-until-proven-innocent]].
