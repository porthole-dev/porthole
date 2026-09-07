---
id: the-taimen-v7-2-tree-was-ten-venus-patches-behind-its-own-aport-series
title: The taimen-v7.2 tree was ten venus patches behind its own aport series, and a venus_core built from it wedges the SoC
scope: device:google-taimen
subsystem: media
severity: trap
confidence: proven
evidence: 2026-09-04 17:50: insmod of the tree's venus-core.ko (BTF neutered, CRCs identical to the installed copy) was followed 6 s later by rtkit 'canary thread starving' and a watchdog reset (bootreason=watchdog). git log v7.2.2..taimen-v7.2 -- drivers/media/platform/qcom/venus listed 3 commits; the aport series carried 13, and WRAPPER_CLOCK_CONFIG existed only in the header. porthole aports patches (dry) would have replaced 210 patches with 197. The refusal itself was ph-modcrc.py dying on a missing llvm-objcopy inside the workspace (exit 1 = 'mismatch'), fixed in porthole 73a248d.
first-learned: 2026-09-04
---

**Symptom** -- `porthole build mod venus-core.ko venus_core --yes` refuses
with "does not share an ABI ... CONFIG skew", the config diff against
`/proc/config.gz` is empty, a hand check with `ph-modcrc.py` reports zero
CRC mismatches, and after a hand `insmod` of the tree's module the phone
logs `rtkit-daemon: The canary thread is apparently starving` six seconds
later and comes back with `bootreason=watchdog`.

**Cause** -- two traps stacked. The tree `linux-ws` (branch taimen-v7.2)
had 197 commits over v7.2.2 while the aport series had 210: the ten venus
bring-up patches (0181-0199: stop_at/clk_limit/boot_stage knobs, the
wrapper clock auto-gating unlock, subcore clocks and GDSCs, the TZ
threshold restore, no power collapse, hfi_trace) and three others never
became commits. A venus_core built without the wrapper unlock touches the
VBIF page and stalls the MMSS NoC -- the exact signature of
[[the-venus-wedge-was-wrapper-clock-auto-gating]]. Separately, the mod
rung's refusal was a false alarm: `ph-modcrc.py` runs inside the workspace,
where there is no `llvm-objcopy`, and its traceback exits 1, which the
rung reads as "CRCs disagree" (fixed: porthole 73a248d prints the checker's
words and treats a missing tool as "cannot compare").

**Do not** insmod a venus_core by hand, ever: `PORTHOLE_MOD_NO_RELOAD`
lists venus_core/dec/enc because the firmware does not survive a
shutdown-and-reboot in one boot ([[venus-decode-works-and-what-it-took]]).
The rung replaces the on-disk copy and the next boot runs it; that is the
designed path, and `tools/ph-reboot.sh` is the reboot.

**The sync that fixed it** (2026-09-04): export the series out of the
container-owned pmaports (`porthole sandbox shell --command 'tar ... |
base64'`), `git worktree add ../linux-series v7.2.2`, `git am` all 210
(0189 and 0203 need `git apply -C1`, abuild's `patch -p1` fuzz had been
hiding the drift), keep a backup ref (`taimen-v7.2-pre-sync-20260904`),
`git reset --hard` the product branch onto the series, cherry-pick the
tree-only commits, then `porthole aports patches --base v7.2.2 --yes`
regenerates the series from the tree (213 patches, r31) so the two agree
again. Before any push, assert `porthole aports patches` (dry) reports at
least as many patches as the series holds.
