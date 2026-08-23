---
id: no-passwordless-sudo-disables-the-whole-toolbox
title: A fresh install has no passwordless sudo, and that silently disables every tool
scope: generic
subsystem: setup
severity: trap
confidence: proven
evidence: taimen AGENTS.md §0.1; porthole `porthole doctor`
first-learned: 2026-08-19
---

**Symptom:** every tool "is broken". Over ssh you get
`sudo: interactive authentication is required` and an empty result.

**Cause:** nearly every tool calls `sudo -n`, and `-n` does not prompt — it just
fails. A `sudoers-nopasswd` file must **not** ship in a device package other
people install, so a fresh install or a reflash silently disables the entire
toolbox.

**Check it first thing in any session that touches the device.** `porthole
doctor` does this and names it as a failure with the fix attached:

```sh
ssh <user>@<host> sudo -n true && echo NOPASSWD_OK
```

**Fix, once per install, on the device and never in a package:**

```sh
echo '<user> ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/99-porthole-dev
sudo chmod 0440 /etc/sudoers.d/99-porthole-dev
```

**An agent whose harness refuses to type a sudo password cannot install this.
Hand it to the human instead of retrying** — that guardrail cost two attempts
before anyone said so out loud.

Related: [[instrument-guilty-until-proven-innocent]].
