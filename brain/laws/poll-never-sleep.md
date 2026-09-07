---
id: poll-never-sleep
title: Poll, never sleep
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: taimen tools/ph-lib.sh header; porthole lib/porthole.sh
first-learned: 2026-07-25
---

A fixed `sleep 60` is wrong in both directions. It wastes 40 seconds when the
device came back in 18, and it reports a failure when the device needed 65.

Both probes worth having are cheap — `fastboot devices` and one ssh round trip
cost about 0.2 s each, and under 20 ms with a warm ssh master — so there is no
excuse for waiting a fixed interval "just in case".

The shape:

```sh
. "$PORTHOLE_ROOT/tools/ph-lib.sh"
OLD=$(tk_boot_id)
... do the thing ...
tk_wait_ssh "$OLD" "$(tk_deadline_ms 300)" && echo up
```

`tk_wait_ssh` polls, auto-recovers if the device lands in the bootloader, and
re-issues a reboot request that was swallowed. A reboot that finishes in 18 s
returns in 18 s.

**The deadline is not the same knob as the poll interval.** Set the deadline
from the worst case you are willing to call a failure; set the poll interval
from what a probe costs. Conflating them is how a 300 s deadline turns into a
300 s wait.

Related: [[wait-long-enough-before-calling-a-boot-failed]],
[[empty-must-mean-unknown-never-changed]].
