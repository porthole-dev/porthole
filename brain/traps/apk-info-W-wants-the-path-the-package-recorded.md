---
id: apk-info-W-wants-the-path-the-package-recorded
title: apk info -W does not resolve /lib -> /usr/lib, and the right path differs for modules and firmware
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: taimen AGENTS.md §1.0b
first-learned: 2026-08-19
---

`apk info -W` answers *"Could not find owner package"* whenever you hand it a
path the package did not record — which reads exactly like a missing package.

There is no "always use /usr/lib" rule. The two cases are **opposites**:

| you are asking about | the path apk answers | the path that lies |
|---|---|---|
| a **module** | `/usr/lib/modules/…/foo.ko` | `/lib/modules/…/foo.ko` |
| **firmware** | `/lib/firmware/qcom/a540_gpmu.fw2` | `/usr/lib/firmware/…` |

The rule is **hand apk the path the package recorded**, and `apk info -L <pkg>`
is what tells you which that is.

Not theoretical: a verification sweep queried
`/usr/lib/firmware/qcom/a540_gpmu.fw2`, got "Could not find owner package", and
wrote down *"the GPU firmware landmine is NOT closed, the file is hand-placed"*
about a file that had been correctly packaged all along.

**And `apk info -W` cannot see hand-edits.** It says a path *belongs* to a
package; it says nothing about whether the bytes still match it. A file edited
in place on the device reports its owner happily. `apk audit --system` prints
`U <path>` for exactly those, and it is the check nobody runs — on taimen it was
the only instrument that noticed a watchdog fix was a live edit on top of an
older package rather than the packaged fix. A reflash or `apk fix` would have
silently undone it.

Related: [[stale-dev-package-outranks-your-build]].
