---
id: evidence-discipline
title: Evidence discipline
scope: generic
subsystem: method
severity: technique
confidence: proven
evidence: taimen docs/BLUEPRINT/BP-11-RESULTS.md; docs/campaign/
first-learned: 2026-08-08
---

A bring-up runs for months and every conclusion is provisional. What survives is
evidence, not verdicts.

## Rules

**Separate observation from conclusion, always, in writing.** "dmesg shows X"
and "therefore the driver is broken" are two different claims with two different
lifetimes. The first stays true.

**Date everything, and prefer absolute dates.** "last week" is unreadable in a
month, and an agent reading your handoff has no idea when you wrote it.

**A status without evidence is not a status.** "WiFi works" decays into a lie.
"WiFi: associated, survived 40 rekeys and 20 suspend cycles on 2026-08-21, zero
restarts" stays useful and is falsifiable.

**Record refuted hypotheses, with what refuted them.** They are as valuable as
the confirmed ones and are otherwise re-tested by the next person — or by you,
in three weeks. If the refutation was weak, say that too.

**A tool should print evidence, not a verdict.** `ph-daily-audit.sh` does this
deliberately: a tool that prints PASS invites you to skip reading the output,
and a wrong PASS is worse than no tool.

**Keep the raw capture.** A summary you cannot re-derive is a summary you cannot
re-audit. On taimen the stock-Android camera captures are kept in-tree
specifically because they are unreproducible without reflashing stock.

Related: [[instrument-guilty-until-proven-innocent]], [[handoff-format]].
