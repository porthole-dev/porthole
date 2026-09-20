---
id: an-instrument-that-fails-quietly-is-worse-than-none
title: An instrument must report 'I could not run' differently from 'I ran and saw nothing'
scope: generic
subsystem: tooling
severity: trap
confidence: proven
evidence: three separate failures in one session, 2026-09-20, all on the taimen double-tap test: a /proc/interrupts parser summing a GPIO pin number as 125 interrupts; an awk program inside sh -c whose busybox syntax error was printed as 'nobody touched the phone in 600s'; and a positive control that was invalid in the armed configuration it was meant to police
first-learned: 2026-09-20
---

**Symptom** — a test prints a confident, device-shaped conclusion, and it is
describing the harness. Three of them in one session, all on the same probe:

```
NO RESULT: nobody touched the phone in 600s      <- it had run for 3 seconds
ash: line 1: syntax error: bad for loop variable <- the real event, one line up
```

```
>> ftm4 irq count before: 125    ...    after: 125
```
The real count was 0 both times. The parser was summing every numeric field of
a `/proc/interrupts` row, and `msmgpio 125 Level ftm4` contributed the PIN
NUMBER. A dead touch controller looked busy, and a busy one would have looked
unchanged.

**Why** — three distinct defects, one shape. The instrument had no way to say
"I did not run", so every failure came out in the vocabulary of a result.

1. **The parse was shaped like the data it expected, not like the data.** Take
   exactly as many count columns as the `CPU0 CPU1 ...` header names. Stopping
   at the first non-numeric field is the version that reads a pin number.
2. **A program quoted into a shell quoted into ssh.** `awk '...'` inside
   `sh -c '...'` inside `tk_run` collides on the single quotes, and busybox ash
   says so on stderr while the caller only checks the exit status. Ship a FILE
   and run it (`scp` then `python3 /tmp/probe.py`); do not quote a program.
3. **A positive control that is invalid in the configuration it polices.** The
   control here was "the touch interrupt moved, so a finger is present" -- but
   it was taken while the controller was ARMED, and an armed controller is
   supposed to stay silent for a non-gesture touch. The control has to be taken
   in the unarmed state, first, as its own phase.

**Instead** —

- Give "could not run" its own exit code, distinct from "ran, saw nothing"
  (`65` vs `1` in `ph-dt2w-test.sh`), and make the caller print a different
  sentence for each. Never let a non-zero from the transport fall through to
  the "nothing happened" branch.
- Take the positive control BEFORE arming, in a state where it is valid, and
  fail the run if it does not fire.
- Prefer a script on the device over a program embedded in a command line. The
  quoting bug is silent, the file is not.
- An unattended test that needs an operator must WAIT for them, not announce a
  timestamp. Two runs measured nothing because the schedule was printed into a
  scrollback nobody was reading.

See [[every-test-needs-a-positive-control]]; this is its instrument-side twin.
