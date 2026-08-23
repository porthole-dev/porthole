---
id: empty-must-mean-unknown-never-changed
title: An empty reading means unknown, never changed
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: porthole lib/porthole.sh tk_boot_id; taimen 20-cycle camera test, 2026-08-19
first-learned: 2026-08-19
---

When you use a value as a baseline, a failed read must not be able to
masquerade as a changed value.

**The worked example.** `tk_boot_id` reads
`/proc/sys/kernel/random/boot_id` to tell whether a device rebooted. A single
timed-out read returns empty — and an empty string compares unequal to every
real boot id. So a caller that took its *baseline* through one transient failure
sees "the device rebooted" on the very next read, and on every read after that,
forever.

That is exactly what happened: sshd was stalling ~7.9 s answering a trivial
command, the 6 s timeout ate the baseline, and a 20-cycle camera test printed
`BOOT_ID CHANGED` and `FAIL` against a phone that had not rebooted once.

Two things fix it, and you need both:

1. **Retry, so empty is rare.** `tk_boot_id` tries twice.
2. **Put the timeout above the known stall, or retrying buys nothing.** Two
   attempts at 12 s, not three at 6 s. Three attempts that each fail at 6 s
   against a 7.9 s stall is just a slower failure.

Cost when the device is genuinely gone is nothing: with the USB interface down
ssh fails instantly, so the retry is free. The long worst case is only spent on
a device that accepts the connection and then stalls — which is precisely the
case worth waiting for.

Generalise: any sentinel that means "I could not read this" must be
distinguishable from every legal value. If your sentinel is `""`, `0`, or `-1`,
check what a legal value can be.

Related: [[every-test-needs-a-positive-control]], [[poll-never-sleep]].
