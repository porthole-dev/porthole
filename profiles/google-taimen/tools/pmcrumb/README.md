# taimen_pmcrumb — resume-hang witness (DO NOT MERGE)

An out-of-tree module that survives a watchdog reset by keeping a 16-bit hash of
the in-flight device's name in the PM8998 PON spare registers (0x88c/0x88d).
arm64 has no `PM_TRACE_RTC`; this is its equivalent over SPMI. Read the header
of `taimen_pmcrumb.c` for the decode rule.

Cited by `docs/HANDOFF-2026-08-24-BUG-A.md`,
`docs/HANDOFF-2026-08-25-BUG-A-FIXED.md`, `docs/BLUEPRINT/BP-14-suspend-endgame.md`
and three others. It lived only in `linux/pmcrumb/`, untracked inside a kernel
checkout, until 2026-08-27 -- one `git clean -xfd` from gone, while six documents
depended on it. The source is tracked here; the build directory is scratch.

    make -C <kernel-tree> M=$PWD modules

`taimen_pmcrumb.v8.c` is the previous revision, kept because the v9 header
records what changed and why (the phase crumb reads `timekeeping_freeze` END
every time, so Bug A dies in the microseconds-long awake window after a
micro-wake, not in the frozen window between them).

Not for the series: the module writes two SPMI registers per PM callback,
~2k writes per suspend cycle. A diagnostic, not a shipping change.
