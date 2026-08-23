---
id: a-journal-grep-matches-your-own-command-line
title: A journal grep counts the grep that is asking
scope: generic
subsystem: diagnosis
severity: trap
confidence: proven
evidence: taimen AGENTS.md §3b
first-learned: 2026-08-19
---

`sudo` logs the full command line it ran. So

```sh
journalctl | grep -c 'Failed to set watchdog hardware timeout'
```

counts **the grep that is asking**, plus every earlier invocation of it still
sitting in this boot's journal.

Measured on taimen: a post-install check reported 1 EINVAL line on a watchdog
that had armed cleanly, then 2 after the next run. The count tracked how many
times the question had been asked. Same family as `pkill -f <pattern>` matching
your own ssh command line.

**A bracket pattern does not save you here.** It hides the current invocation
while earlier ones are already in the journal with the plain string.

Filter by who emitted the line — and prove the filter is non-empty first:

```sh
journalctl -b 0 --no-pager -t systemd | grep -c 'systemd\[1\]'   # control: must be > 0
journalctl -b 0 --no-pager -t systemd | grep 'systemd\[1\]' | grep -c '<the string>'
```

`_PID=1` looks like the obvious filter and returned an **empty set** on this
device, which turns the count into a null with the path never exercised — the
exact failure this note exists to prevent. Print the filter's own total before
you trust a zero from it.

Related: [[every-test-needs-a-positive-control]].
