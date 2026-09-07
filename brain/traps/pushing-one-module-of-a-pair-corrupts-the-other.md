---
id: pushing-one-module-of-a-pair-corrupts-the-other
title: Pushing one module while its sibling stays old is worse than pushing neither
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: "taimen 2026-08-29. A header change widened struct fields in qmi_sns_smgr.h. That header is compiled into TWO modules -- qcom_smgr.ko (which allocates the structs) and qmi_sns_smgr.ko (which holds the qmi_elem_info arrays whose offsetof() describes them). Only qcom_smgr.ko was pushed. The device then took a watchdog reset every boot, with an empty journal and androidboot.bootreason=watchdog."
first-learned: 2026-08-29
---

**Do not push a subset of the modules a change rebuilt.** MODVERSIONS does not
save you here, and that is the whole trap: it checks *symbol* CRCs, and a
struct that never appears in an exported symbol's signature has no CRC to
disagree about. Two modules can therefore load happily while disagreeing about
where the fields of a shared struct live.

What it looks like: the device boots, hangs before userspace, and resets. No
oops, no panic string, nothing in `journalctl -b -1` -- the watchdog fires, so
there is no orderly death to log. `/proc/cmdline` of the next boot carries
`androidboot.bootreason=watchdog`, and that is the only witness you get for
free. `pstore` is empty on many devices; netconsole (`tools/ph-capture.sh`)
is the channel that survives.

Why it is worse than pushing nothing: a stale set is at least self-consistent.
A mixed set has one module writing a struct at the new offsets while another
reads it at the old ones, which is memory corruption with a plausible-looking
cause somewhere else entirely. The first instinct is "my code change is
wrong", and it need not be -- the change that caused this was correct and
shipped unaltered once *both* modules were pushed.

**The rule:** push every module the build touched, not the one you were
thinking about. `modinfo -F depends <module>` names its siblings, and a header
under a driver's directory is compiled into every module in that directory.

`tools/ph-push-module.sh` now warns when a module you are pushing has
siblings on the device that differ from the ones next to it in the build, so
this specific mistake announces itself instead of costing a rescue.
