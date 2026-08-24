---
id: a-shipped-default-is-not-an-answer
title: A shipped default is indistinguishable from a real answer, and safety checks complete themselves on it
scope: generic
subsystem: bringup
severity: trap
confidence: proven
evidence: porthole 2026-08-24. Two instances found in one afternoon. `profiles/_template/device.env` ships PORTHOLE_REBOOT_BUDGET_S="120" and PORTHOLE_HAS_AB_SLOTS="0"; probes reading them reported an unmeasured budget as MEASURED and auto-completed the A/B slot-policy milestone on every freshly scaffolded device. Fixed by `_established()` (value != template default) and by PORTHOLE_SLOTS_PROBED (a witness that the question was asked).
first-learned: 2026-08-24
---

A config key that is non-empty tells you nothing on its own. If the template
ships a value, then "the user established this" and "nobody has touched it"
produce **byte-identical** state, and any check reading that key answers the
wrong question.

This is worse than a missing value, because a missing value is visibly unknown
and a defaulted one looks settled. It cost two bugs in one afternoon:

- `PORTHOLE_REBOOT_BUDGET_S="120"` — a budget nobody measured, reported as
  measured. Every unattended wait then uses a number with no evidence behind it.
- `PORTHOLE_HAS_AB_SLOTS="0"` — **the dangerous one.** The A/B slot-safety
  milestone completed itself on every scaffolded device, including devices that
  do have slots. Its own stated reason is *"recovery depends on a known-good
  image on the other slot; after the first bad flash is too late to decide
  this"*. A safety check that completes itself is worse than no check at all,
  because it also reports that the matter is handled.

Two fixes, and which one you need depends on whether the default is a legal
answer:

1. **The default is not a plausible answer** (a timing budget, a size, a path):
   compare against the template. `value != template_default` is enough, and it
   needs no new state.
2. **The default IS a legal answer** (`"0"` for "no slots" is both the shipped
   value and the truth for many devices): comparing values cannot work. Record
   a **witness that the question was asked** — porthole writes
   `PORTHOLE_SLOTS_PROBED="fastboot-getvar"` when `fastboot getvar all` runs —
   and gate the positive verdict on the witness, not on the value.

The general rule: **when you derive a verdict from configuration, ask what a
never-configured profile produces.** If it produces a pass, the check is
decoration. This is the same idea as
[[empty-must-mean-unknown-never-changed]], one layer up: there, an empty
reading must not read as "unchanged"; here, a *defaulted* one must not read as
"established".
