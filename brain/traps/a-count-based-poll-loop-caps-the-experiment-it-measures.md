---
id: a-count-based-poll-loop-caps-the-experiment-it-measures
title: A count-based poll loop silently caps how long an experiment may take
scope: generic
subsystem: tooling
severity: trap
confidence: proven
evidence: tools/ph-suspend-cycle.sh polled with 'for _ in $(seq 1 60)' and a 6 s ConnectTimeout per probe, so it gave up after ~360 s. On 2026-09-20 a 900 s alarm printed 'ssh: connect to host 172.16.42.1 port 22: Connection timed out' and left tk_device_state() reading ABSENT, while the phone was merely still asleep with 430 s to go on its RTC alarm; it woke on its own at the alarm. Fixed by deriving a deadline from the alarm: DEADLINE=$(tk_deadline_ms $((A + TK_SUSPEND_MARGIN)))
first-learned: 2026-09-20
---

**Symptom** — a long run reports a dead phone:

```
>> suspending (alarm=+900s, prep=none)
>> result
ssh: connect to host 172.16.42.1 port 22: Connection timed out
```

`tk_device_state` then reads `ABSENT`, `fastboot devices` is empty, and it looks
exactly like the resume hang you were hunting. It is not. The phone is still
asleep, and it wakes on its own RTC alarm several minutes later.

**Cause** — the wait was written as a fixed number of probes, not as a
deadline:

```sh
for _ in $(seq 1 60); do
	[ "$(S "grep -q 'TRY DONE' $LOG && echo READY" 12)" = READY ] && break
done
```

Sixty probes at roughly 6 s each (`ConnectTimeout=6` on a host that is not
answering) is about 360 s of patience. Every alarm longer than that reports a
failure it never waited for. Nothing in the loop mentions the alarm, so the cap
is invisible at the call site — `ph-suspend-cycle.sh 7200` looks like a
supported thing to run.

**Fix** — derive the deadline from the experiment's own duration:

```sh
DEADLINE=$(tk_deadline_ms $(( A + ${TK_SUSPEND_MARGIN:-180} )))
until tk_expired "$DEADLINE"; do
	[ "$(S "grep -q 'TRY DONE' $LOG && echo READY" 12)" = READY ] && break
done
```

**The general rule** — a poll loop's limit must be a function of what it is
waiting for. `seq N` encodes a duration in units of "however long a failed
probe happens to take", which changes with the timeout, the network and the
failure mode, and which no reader can convert back into seconds. When the
duration under test is itself the variable being swept — as it is for a
suspend-hang hunt — a fixed cap silently truncates the sweep at the one place
it matters.

**Related** — [[poll-never-sleep]] is about not sleeping instead of polling;
this is the other half: poll to a deadline you can name.
