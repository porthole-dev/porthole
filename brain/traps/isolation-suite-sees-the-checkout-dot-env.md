---
id: isolation-suite-sees-the-checkout-dot-env
title: workdir keys in the checkout-root .env break test_isolation
scope: generic
subsystem: config
severity: trap
confidence: proven
evidence: tests/test_isolation.py runs bin/porthole with PORTHOLE_ROOT pinned to the checkout, so the CLI reads the checkout-root .env (a real config layer); on a working checkout holding PORTHOLE_WORKDIR_GOOGLE_REDFIN plus the bare PORTHOLE_WORKDIR, 4 of 12 isolation tests fail (e.g. "expected no workdir for B, got ..."), and the same run passes 12/12 after moving the keys to ~/.config/porthole/config.env and deleting the .env
first-learned: 2026-08-30
---

**Symptom** — `make check` fails in tests/test_isolation.py on a working checkout, with messages like a device inheriting the global workdir or use reporting a workdir that should be missing. A fresh clone passes the same suite.

**Cause** — the isolation suite pins PORTHOLE_ROOT to the checkout it runs from, and the CLI reads \$PORTHOLE_ROOT/.env as one of its config layers. The suite was written against a clean checkout, so any workdir key you keep in that .env leaks into its synthetic scenarios.

**What to do** — keep per-device workdir keys (PORTHOLE_WORKDIR_<DEVICE>) in \`~/.config/porthole/config.env\`, not in the checkout-root .env. The suite then sees the same empty layer CI does.

Related: [[a-stale-inherited-env-outbuilds-the-profile]].
