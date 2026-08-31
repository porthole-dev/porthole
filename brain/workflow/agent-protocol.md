---
id: agent-protocol
title: How an agent should work on a bring-up
scope: generic
subsystem: method
severity: technique
confidence: proven
evidence: taimen AGENTS.md §0, §3; two ports of accumulated correction
first-learned: 2026-08-19
---

Written for LLM agents, and it applies to humans too.

## Before you touch anything

1. `porthole doctor` — it tells you whether the toolbox will work at all, and
   catches [[no-passwordless-sudo-disables-the-whole-toolbox]] before you spend
   an hour on "every tool is broken".
2. **List the whole tools directory before concluding a tool does not exist.**
   There are over a hundred. A truncated listing has caused exactly that mistake.
3. Read `brain/playbooks/00-device-protocol.md` once.

## While you work

The rules themselves live in `lib/porthole_rules.py` with their levels and
enforcers, and reach you through `porthole brief --json`. They were restated
here once, and this copy had drifted: it carried four of the ten. Cited by id
now, because a rule written in two voices is a rule a reader gets to choose
between.

`no-hand-rolling` — if you are writing an `ssh ... reboot` one-liner or a
`sleep 60`, there is a tool and you have not found it yet.

`device-mutex` — [[the-lock-says-who-not-what]].

`hand-back-a-device-you-did-not-set` — do not recover someone else's
experiment out from under them.

`ssh-timeout-on-reset` — "the device stopped answering" is your expected
outcome there, and a command without a timeout wedges the lock against every
other agent.

## Before you report a result

This is where most of the damage happens. Ask, in order:

1. What proves the code under test actually ran?
   [[every-test-needs-a-positive-control]]
2. If this is a null, what would look different had the path never executed?
   [[a-null-from-an-unexecuted-path-is-not-a-refutation]]
3. Which kernel answered? [[prove-which-kernel-answered]]
4. Is my instrument capable of seeing the thing I am claiming is absent?
   [[dmesg-can-be-empty-about-boot]],
   [[a-journal-grep-matches-your-own-command-line]]

**Report what you observed, then what you concluded, separately.** A handoff
that mixes them cannot be re-audited when the conclusion turns out wrong — and
on a bring-up, conclusions turn out wrong constantly. That is fine. Conclusions
that are indistinguishable from observations are not.

## Do not give worktree isolation to work that touches nested repos

A worktree of the outer repo does not contain nested repos at all, and every git
operation against their real paths is refused from inside it. An agent given
that setup can `ls` the files and do nothing else — it will burn a long time and
return BLOCKED.

Related: [[handoff-format]], [[evidence-discipline]].
