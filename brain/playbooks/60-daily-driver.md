---
id: 60-daily-driver
title: "Playbook: the daily-driver push"
scope: generic
subsystem: method
severity: technique
confidence: proven
evidence: taimen docs/PLAN-daily-driver-v2.md, docs/BLUEPRINT/BP-10-ROADMAP.md
first-learned: 2026-08-08
---

**Goal:** the device is good enough to carry.

**Done when:** you carry it. There is no other test, and every proxy for it
lies.

## What actually blocks it

In the order they bit on taimen, which is not the order anyone expects:

1. **Suspend.** A phone that does not sleep is a phone that is dead by lunch.
   This dominates everything else. [[40-suspend]]
2. **Stability under real use**, not under test. Statistical bugs that a
   deliberate test never triggers.
3. **Performance.** App launch, scroll, wake latency. Measure two runs per arm
   and know your noise floor — [[critical-chain-shows-the-longest-path-not-the-floor]]
   is the cautionary tale.
4. **The radios.** [[50-wifi-bt-modem]]
5. **Camera.** Almost always last, almost always the hardest.

## The discipline that makes this phase work

**Measure before and after, with a noise floor.** A performance change without a
noise floor is a story. Two identically-configured boots on taimen differed by
0.35 s, which is larger than most of the "improvements" anyone proposed.

**Audit rather than assume.** `tools/ph-daily-audit.sh` prints evidence, not
verdicts — deliberately. A tool that prints a verdict invites you to skip
reading the evidence.

**Keep a support matrix with evidence attached.** "WiFi works" is not a status.
"WiFi: associated, survived 40 rekeys and 20 suspend cycles on <date>, zero
restarts" is.
