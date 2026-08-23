---
id: no-cgroup-io-control-without-kernel-config
title: cgroup I/O control is inert unless the kernel config enables it
scope: soc:msm8998
subsystem: performance
severity: fact
confidence: proven
evidence: taimen AGENTS.md §3b
first-learned: 2026-08-19
---

On the taimen kernel config, `io.weight`, `io.max`, `io.latency`, systemd's
`IOWeight=` / `IOReadBandwidthMax=`, and `IOSchedulingClass=` are **all inert**.
Do not design a fix around them without changing the kernel config first.

```
CONFIG_BLK_CGROUP=y                     # accounting only
# CONFIG_BLK_CGROUP_IOLATENCY is not set
# CONFIG_BLK_CGROUP_IOCOST is not set
# CONFIG_BLK_CGROUP_IOPRIO is not set
# CONFIG_BFQ_GROUP_IOSCHED is not set
```

On device this shows as `/sys/fs/cgroup/` carrying only `io.pressure` and
`io.stat` — there is no `io.weight` to write. And
`/sys/block/sda/queue/scheduler` selects `mq-deadline`, which ignores ioprio
entirely, so `IOSchedulingClass=idle` is a no-op too. Only `bfq` honours ioprio,
and it is built but not selected.

Consequence: a `background.slice` can be CPU-nice'd but has **no I/O protection
at all**, and none can be added from userspace on this kernel.

**The general check, on any device, before designing around a cgroup knob:**
`ls /sys/fs/cgroup/` and confirm the file you intend to write actually exists.

Related: [[shipped-configuration-is-not-running-configuration]].
