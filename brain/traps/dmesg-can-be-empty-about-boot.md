---
id: dmesg-can-be-empty-about-boot
title: dmesg can be empty about boot while the journal still has everything
scope: generic
subsystem: diagnosis
severity: trap
confidence: proven
evidence: taimen AGENTS.md §3b; ath10k_snoc WARN storm, 2026-08-19
first-learned: 2026-08-19
---

**Symptom:** `dmesg | grep -i adreno` returns nothing on a device whose GPU is
demonstrably working. Nothing errors. The empty result reads as "the driver
never probed".

The kernel log ring is a fixed-size buffer. An `ath10k_snoc` WARN storm — 12 ×
*"Unbalanced enable for IRQ"* with a full register dump and backtrace on every
WiFi firmware restart — pushed the oldest surviving `dmesg` line to **t=195 s of
a ~2000 s uptime**. Everything about boot was gone. `journalctl -b 0 -k` still
had all of it, because journald had already persisted it.

```sh
dmesg | head -1    # if this is not near [    0.0], dmesg cannot answer any
                   # question about boot -- use journalctl -b 0 -k
```

Check that one line before concluding anything from a `dmesg` grep. **Any device
that logs a repeating WARN puts every agent after it in this position**, which
makes this one of the most reusable checks here.

Related: [[instrument-guilty-until-proven-innocent]].
