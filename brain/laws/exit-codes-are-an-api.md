---
id: exit-codes-are-an-api
title: Exit codes are an API — distinguish retryable from not
scope: generic
subsystem: method
severity: law
confidence: proven
evidence: porthole tools/ph-device.sh; bin/porthole docstring
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
| 69 | the tool could not run at all | **not a finding** — the check did not happen |
| 75 | could not get the device lock | **retry** — someone else has it |
| 76 | device in the wrong state | **do not retry** — something must move it |
| 124 | killed at the hold ceiling | a wedge; investigate, do not just rerun |

The 75/76 split is the one that earns its keep, and it exists because the
alternative cost two agents ten minutes each. Waiting fixes a 75. Waiting never
fixes a 76.

Keep 1 for "the measurement says no", and 69 for "no measurement happened".
That split is not decoration: `porthole aports lint` printed "lint found
problems" for a subcommand pmbootstrap 3.11.1 had removed, and `porthole
channel <name> --yes` printed "pmbootstrap refused the channel change" for a
config key that no longer exists. Both were exit 1 -- a broken tool wearing the
costume of a finding about the user's work. An agent that cannot tell the two
apart reports broken tools as findings, and the porter fixes a package that was
never wrong.

So: if the tool could not run -- not installed, subcommand gone, no workspace
and no host toolchain -- exit 69 and say what is missing. Never render an
unavailable check as a negative answer.

Related: [[the-lock-says-who-not-what]].
