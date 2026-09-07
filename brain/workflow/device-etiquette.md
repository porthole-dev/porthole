---
id: device-etiquette
title: Sharing one physical device between several workers
scope: generic
subsystem: method
severity: technique
confidence: proven
evidence: porthole tools/ph-device.sh; taimen AGENTS.md §3, 2026-08-19
first-learned: 2026-08-19
---

There is one phone. There may be several agents, or an agent and a human.

**Every command that touches it goes through the mutex:**

```sh
TK_AGENT=<yourname> tools/ph-device.sh <command>
```

`flock`, not a hand-rolled lockfile: it is race-free across processes and the
kernel releases it when the holder dies. A hand-rolled lockfile gets exactly
that case wrong, and a crashed agent then wedges everyone until someone notices.

**Declare the state you need**, so you fail in a second rather than queueing ten
minutes for a device that was never going to answer:
[[the-lock-says-who-not-what]].

**Name yourself.** `TK_AGENT` goes in the holder file, so the next worker's
timeout message says who has it and what the device was doing.

**Two timeouts, and they are different things.** How long you wait for the lock
(exit 75) versus how long you may hold it (exit 124). The hold ceiling exists to
break wedges, not to discipline long measurements — a CPU benchmark legitimately
held the device for well over ten minutes. A ceiling that kills a valid
measurement is a worse bug than the one it fixes, so the default is generous and
a caller that needs longer says so explicitly.

**Hand back what is not yours.** Finding the device in the bootloader usually
means someone is mid-experiment, not that something is broken.
