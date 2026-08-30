---
id: memory-high-arms-systemd-oomd-against-the-browser
title: MemoryHigh= on an app scope arms systemd-oomd against that app
scope: generic
subsystem: memory
severity: trap
confidence: proven
evidence: 2026-08-30 taimen: set MemoryHigh=1500M on a live Epiphany scope sitting at 2101M; forced reclaim drove the scope's pressure to Avg10 89.70 and 45s later systemd-oomd killed 181 processes in the unit -- 'memory pressure for /user.slice/.../app.slice being 89.57% > 50.00% for > 30s with reclaim activity'. app.slice in the user manager ships ManagedOOMMemoryPressure=kill with limit 2147483648 (=50% of UINT32_MAX).
first-learned: 2026-08-30
---

**Do not set `MemoryHigh=` on an app scope that is already over the limit.**
Set it at launch instead. Retrofitting it onto a running process tree forces the
kernel to claw back the difference immediately, and that reclaim storm is
indistinguishable — to systemd-oomd — from an app running away with memory.

What happened on taimen, 2026-08-30:

    01:58:37  MemoryHigh=1500M applied to a live Epiphany scope at 2101M
    01:58:38  three WebKitWebProcesses log "Memory pressure relief" (working!)
    01:59:22  systemd-oomd: pressure Avg10: 89.70
              "Marked .../app.slice/app-...Epiphany-38834.scope for killing
               due to memory pressure for .../app.slice being 89.57% > 50.00%
               for > 30s with reclaim activity"
    01:59:22  systemd-oomd killed 181 process(es) in this unit
    01:59:24  Failed with result 'oom-kill'

**The two mechanisms are in direct tension by design.** `memory.high` works *by*
generating reclaim pressure; systemd-oomd is configured to kill things that
generate reclaim pressure. The user manager ships `app.slice` with:

    ManagedOOMMemoryPressure=kill
    ManagedOOMMemoryPressureLimit=2147483648   # = 50% of UINT32_MAX

Stock GNOME gets away with shipping both only because nothing normally sets
`memory.high`. The moment you do, you have partly armed oomd against your own
mitigation.

**Applied at launch this does not fire**, because the app never has a cliff to
fall off — it grows into the limit and sheds as it goes. Verified: zero oomd
kills across the following hour with the limit in place from startup.

**If it does fire in steady state** — meaning the app genuinely cannot live
under the limit and sits pinned at the ceiling in permanent reclaim — the fix is
`ManagedOOMPreference=omit` on that scope, *not* raising the limit until the
original bug returns. Do not add that line pre-emptively: it disables the only
thing that would catch a real runaway, and steady-state oscillation is one
measurement away rather than a thing to guess at.

**The generic lesson:** a resource limit and a resource-pressure killer are the
same signal read two ways. Before adding a cgroup limit, check what is watching
that cgroup's pressure -- `systemctl show <unit> -p ManagedOOMMemoryPressure
-p ManagedOOMMemoryPressureLimit`.
