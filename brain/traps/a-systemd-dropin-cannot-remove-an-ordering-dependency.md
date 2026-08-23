---
id: a-systemd-dropin-cannot-remove-an-ordering-dependency
title: A systemd drop-in cannot remove an ordering dependency
scope: generic
subsystem: userspace
severity: trap
confidence: proven
evidence: taimen AGENTS.md §3b; systemd-user-sessions drop-in, 2026-08-19
first-learned: 2026-08-19
---

`After=` / `Before=` / `Wants=` / `Requires=` can only be **ADDED** by a
drop-in. The empty-assignment reset that works for most list settings does not
remove ordering.

The drop-in installs. `systemctl daemon-reload` is clean. The file is on disk.
The dependency is still there.

Measured: a `systemd-user-sessions.service.d/` drop-in with `After=` followed by
a shorter list was installed to unhook the greeter from `network.target`.
`dropin=PRESENT`, no error anywhere — and
`systemctl show systemd-user-sessions.service -p After` still listed
`network.target`. The boot number then moved by nothing, **which reads exactly
like "the theory was wrong" rather than "the change never happened"**.

```sh
# the control. Not optional -- the drop-in existing proves nothing.
systemctl show <unit> -p After --value | tr ' ' '\n' | grep -c '^<dep>$'
```

Removing ordering needs a **full unit override** in `/etc/systemd/system/`,
which forks an upstream unit — price that maintenance cost before assuming the
fix is cheap.

Related: [[shipped-configuration-is-not-running-configuration]],
[[every-test-needs-a-positive-control]].
