---
id: 90-upstreaming
title: "Playbook: getting the work upstream"
scope: generic
subsystem: method
severity: technique
confidence: proven
evidence: taimen AGENTS.md §6; docs/campaign/UPSTREAM-READINESS.md
first-learned: 2026-08-08
---

**Goal:** the port is not a permanent downstream fork.

**Done when:** someone else's tree carries your work.

## The rule that shapes everything else

**Upstream-first: extend an existing upstream driver rather than forking it.** A
permanent downstream mirror of upstream code is the failure state. Fall back to
a local patch only when extending would be disproportionate — and say so in the
commit message.

## Write every commit as if you were sending it today

Because eventually you will, and rewriting history later is worse than writing
it right.

- Subsystem subject prefixes: `arm64: dts: qcom: <soc>: …`,
  `media: qcom: camss: …`, `iio: …`, `Input: <driver> - …`, `dt-bindings: …`
- Imperative mood, ≤ ~72 chars, no trailing period
- `scripts/checkpatch.pl --no-tree --strict` clean before it counts as done
- A driver goes in **with** its Kconfig/Makefile hunks and its dt-binding, in
  the same commit — that is how upstream takes it
- Bindings are YAML under `Documentation/devicetree/bindings/`
- The series must be **bisectable**: every commit builds
- **Anything that benefits other SoCs is split into its own commit.** Those are
  independently upstreamable and should be sent separately

## What must not appear in a submitted patch

Bring-up scaffolding. Debug module parameters. `tk_*` helpers. Commented-out
experiments. Diary-style comments. Keep two branches if you need to: one that is
the development record, one that is the product.

## Parity obligations

Every device-specific systemd unit needs an OpenRC equivalent, so a developer
choosing OpenRC gets the same fixes.

Related: [[workflow/commit-conventions]].
