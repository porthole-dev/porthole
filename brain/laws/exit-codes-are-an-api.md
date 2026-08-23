---
id: exit-codes-are-an-api
title: Exit codes are an API — distinguish retryable from not
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: porthole tools/tk-device.sh; bin/porthole docstring
first-learned: 2026-08-19
---

A tool that returns 1 for everything forces its caller to parse English. In an
agentic loop that means retrying things that will never succeed, and giving up
on things that would have worked on the second try.

The convention porthole uses, and any tool in it must follow:

| code | meaning | caller should |
|---|---|---|
| 0 | success | continue |
| 1 | the thing under test failed | report it — this is a result, not an error |
| 64 | usage error | fix the invocation |
| 75 | could not get the device lock | **retry** — someone else has it |
| 76 | device in the wrong state | **do not retry** — something must move it |
| 124 | killed at the hold ceiling | a wedge; investigate, do not just rerun |

The 75/76 split is the one that earns its keep, and it exists because the
alternative cost two agents ten minutes each. Waiting fixes a 75. Waiting never
fixes a 76.

Keep 1 for "the measurement says no". An agent that cannot tell "the tool broke"
from "the answer is no" will report broken tools as findings.

Related: [[the-lock-says-who-not-what]].
