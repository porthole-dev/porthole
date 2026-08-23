---
id: critical-chain-shows-the-longest-path-not-the-floor
title: systemd-analyze critical-chain shows the longest path, not the floor
scope: generic
subsystem: userspace
severity: trap
confidence: proven
evidence: taimen AGENTS.md §3b; greetd measurement, 2026-08-19
first-learned: 2026-08-19
---

`critical-chain` answers *"what did this unit wait for"*, not *"how early could
this unit have started"*. Subtracting a link does **not** predict the saving,
because the next-longest path is still there.

Measured: `critical-chain greetd.service` showed
`greetd -> systemd-user-sessions -> network.target -> NetworkManager ->
taimen-modem-bringup (+1.534 s)`, reading as ~2.5 s of recoverable boot.
Removing the `network.target` link for real bought **0.67 s** — against a
**0.35 s** boot-to-boot noise floor measured from two identically-configured
boots. And `graphical.target` got **1.1 s worse**.

So:

- **Never quote a saving from `critical-chain`.**
- **Measure two boots per arm**, because you need the noise floor before any
  number means anything.
- **Check the target you actually care about did not regress** while you
  optimised a unit earlier in the chain.

greetd's real floor was `plymouth-quit-wait.service`; it still started ~1.0 s
after `basic.target` with the dependency gone.

Related: [[every-test-needs-a-positive-control]].
