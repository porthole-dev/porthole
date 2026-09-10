<!-- porthole | design | 2026-09-09 -->
# Fork provenance, upstream drift, and moving between hosts

**Date:** 2026-09-09
**Status:** built. Plans 1, 2 and 3 are all landed.

The `porthole:design-doc` marker is GONE from this file as of 2026-09-10:
every command named below now parses, so the exemption it granted from
`tests/test_documented_commands.py` would from here on hide a real break
rather than describe unbuilt work. That was the condition written here for
removing it.

Two deliberate deviations, each recorded in its own section: §9's branch
expectation reports rather than asserts, and §9's actions are positional
rather than `--in`/`--out`.

Written after auditing what google-taimen actually carries, against
`pmaports@perf/crossdirect-native-link` and `aports_upstream@master`
(last fetched 2026-08-20). The implementation plan is a separate document;
§14 sequences it as three.

---

## 1. What happened

Two questions were asked in the same session, and they turned out to be the
same question.

**"Does whoever builds this device get the good stuff?"** Partly, by luck.
`ph-pkgcheck.sh` guards four aports:

```sh
OWNED=(device-google-taimen "$KERNEL_APORT" phoc gst-plugins-good)
```

`temp/mesa` is not among them. It carries `a5xx-tile-init-msaa.patch`,
`a5xx-msaa-sysmem.patch` and `a5xx-tile-parity.patch` — the root-cause fix for
the panel strip and for unresolved MSAA GMEM stores. Nothing checks that it
ships. Neither is `libcamera`, and the device is running **r3 against a fork at
r18**; nor `firmware-google-taimen`.

**"When upstream moves, what happens to my patches?"** They lose, silently:

```
aports_upstream/main/mesa   26.1.6-r0
pmaports/temp/mesa          26.1.6-r14
```

The fork wins today only because the `pkgver` matches and apk then compares
`pkgrel`. apk compares `pkgver` **first**: the day Alpine ships `26.2.0-r0`,
that outranks `26.1.6-r14` and all three patches vanish with no message.

This is not hypothetical. `ph-pkgcheck.sh` documents it happening:

> temp/phoc sat at 0.56.0 while the mirror moved to 0.57.0, apk took the newer
> stock build, and both GPU-reset patches vanished with no message anywhere.
> Nine hours of corrupt session followed, diagnosed as a WebKit bug.

And the fork records nothing about where it came from. `temp/mesa/APKBUILD`
opens with Alpine's original contributor line and no marker of which tree, which
path, or which version it was taken from. **A rebase has no base.**

## 2. Diagnosis

porthole has **no model of provenance**: it never records that a local thing was
derived from a remote thing at a known point.

The gap appears twice, at two scales:

| scale | local thing | derived from | reconciliation | today |
|---|---|---|---|---|
| package | `temp/mesa` | `aports_upstream/main/mesa` at `26.1.6-r0` | rebase | nothing |
| workstation | this checkout | the remote, and the other PC | sync | nothing |

The verbs that look like they should cover it do not:

| verb | what it actually does | why it does not close the gap |
|---|---|---|
| `porthole pkg outdated` | does my aport match the `.apk` I built from it | local build freshness; never looks upstream, by explicit design |
| `porthole pkg fork` | copies an Alpine aport into pmaports | records no provenance, so the next question is unanswerable |
| `porthole aports` | status, branches, diffs, patches | *"Everything here is read-only or an ordinary git operation. Nothing rewrites history and nothing pushes."* |
| `porthole init` | identity, address, paths | config, not content. `NEW-HOST.md` calls it "the whole setup" |

A third symptom belongs to the same root. `docs/BUILD-RUNBOOK.md` says pmaports
must be on `taimen-bringup`; it is on `perf/crossdirect-native-link`, and
`taimen-bringup` is **strictly behind** — zero commits it does not have. Anyone
following the runbook today silently loses six commits. Which branch is correct
is a device fact living in prose instead of in config.

## 3. Goals

1. A fork knows where it came from, and says so in the tree.
2. Upstream moving past a fork is **loud and early**, never discovered by
   debugging the symptom nine hours later.
3. Rebasing a fork is offered, never automatic — the conflicts need judgement.
4. Whoever builds this device gets a single list of what it carries and **what
   breaks without each piece**.
5. Moving between two workstations is one command per direction, and refuses
   rather than guesses.

## 4. Non-goals

- **No automatic rebasing.** Detection is always right; rebasing needs a human.
- **No history rewriting, no force-push, no invented commits.** Ever.
- **Not a package manager.** apk decides what installs; this only makes apk's
  decision visible before it surprises anyone.
- **The kernel tree is not synced.** It is deliberately not mirrored; pmaports
  carries the series. `sync` will say so rather than silently skipping it.
- **chromium is not carried.** It does not open a window on this device
  (`brain/findings/chromium-segfaults-because-a-phone-sends-no-xkb-keymap`).

## 5. The manifest — `profiles/<codename>/aports.conf`

Same grammar as the profile's existing `capabilities.conf` and `probes.conf`:
a bare name, then indented `key: value`. No new format, no TOML.

```
# What this port carries on top of stock, and what breaks without each.
#
#   <aport>
#     upstream: <tree path>        where it was forked from, in aports_upstream
#     tier:     required|optional  optional = real, but a multi-hour build
#     why:      <what breaks>      one line minimum; this is the whole point
#     forked:   <pkgver-pkgrel>    upstream's version at fork time
#     commit:   <sha|unknown>      aports_upstream commit at fork time

mesa
  upstream: main/mesa
  tier:     required
  why:      a5xx-tile-init-msaa, a5xx-msaa-sysmem, a5xx-tile-parity. Without
            them the panel strip returns and MSAA GMEM stores go unresolved.
  forked:   26.1.6-r0
  commit:   unknown (backfilled 2026-09-09)
```

Four consumers, one file:

| consumer | uses |
|---|---|
| `ph-pkgcheck.sh` | the name list, replacing its hardcoded `OWNED=` |
| `porthole pkg drift` | `upstream:` and `forked:` |
| `porthole pkg rebase` | `upstream:`, `forked:`, `commit:` |
| a human arriving at this port | `why:` and `tier:` |

`tier: optional` is how `webkit2gtk-6.0` and `epiphany` are named and explained
without being checked by default: they are real, and they are multi-hour builds.
`ph-pkgcheck.sh` checks `required` unless asked for all.

The list is data in the **profile**, not a bash array in a `scope: soc:msm8998`
tool, because it is a device fact — the `no-hardcoded-values` rule.

### 5.1 Which aports, for google-taimen

`temp/` holds 21 directories. Most are pmaports' own staging area, inherited
with the clone and nothing to do with this port. Ours:

| aport | tier | why |
|---|---|---|
| `device-google-taimen` | required | the device package itself |
| `linux-postmarketos-qcom-msm8998-7.2` | required | the kernel and its series |
| `firmware-google-taimen` | required | named in the device's `depends=` |
| `phoc` | required | GPU-reset patches; commit pipelining |
| `mesa` | required | three a5xx patches |
| `gst-plugins-good` | required | venus/dmabuf path |
| `libcamera` | **open — see §11** | AF actuator sign fix |
| `webkit2gtk-6.0` | optional | filters, scroll prepaint; multi-hour build |
| `epiphany` | optional | snapshot crash fix; multi-hour build |

## 6. `porthole pkg drift`

Fetches `aports_upstream` first — it is 20 days stale, so today the answer is
genuinely unknown — then per manifest aport reports the question that bites:

> **would apk prefer upstream's build over ours?**

Not "is there a newer version". The verdict is apk's own version ordering over
`pkgver` then `pkgrel`, so a fork at `-r14` reads as safe against `26.1.6-r0`
and as **losing** against `26.2.0-r0`.

Output names the patches at risk, because that is what the reader actually
cares about:

```
mesa       26.1.6-r14   upstream 26.2.0-r0   LOSES
           3 patches at risk: a5xx-tile-init-msaa, a5xx-msaa-sysmem,
           a5xx-tile-parity
           porthole pkg rebase mesa
```

Wired into `doctor` as a warn line and into `brief`, so an agent starting a
session is told before it forms a theory.

## 7. `porthole pkg rebase <aport>`

Explicit, on request, and it never touches the working branch.

1. Re-fork `upstream:` at the new version into a scratch branch.
2. Replay the delta: the `.patch` files beside the APKBUILD, and the APKBUILD
   diff between `forked:` and ours.
3. Report what applied, what conflicted, and what needs a checksum refresh.
4. Stop. The human finishes it.

**Built 2026-09-10**, as `porthole pkg rebase <aport>`. Three notes on what
the implementation learned:

- Step 1 does not re-fork. `aportgen` clones and needs the workspace; the
  upstream tree is already on disk in `aports_upstream`, so the three trees
  come from `git show` at two refs plus our directory, and `git merge-file`
  does the replay. No container, no network, seconds rather than minutes.
- **Every manifest entry that exists today says `commit: unknown`** -- they
  were backfilled, and §8 only starts recording real ones now. So the base is
  recovered by walking the upstream APKBUILD's history for the `forked:`
  version. Verified: mesa's `26.1.6-r0` is `e744e23b`. Where that fails,
  rebase REFUSES rather than picking a base, because rebasing onto the wrong
  one silently reclassifies upstream's changes as our delta.
- "Never touches the working branch" is a `git worktree` on a fresh branch,
  written into `PORTHOLE_RUNDIR` rather than into pmaports -- a worktree
  inside pmaports is an untracked directory that `porthole sync` (§9) then
  correctly refuses to sync past.

First real run, mesa 26.1.6-r14 onto 26.2.2-r0: the three a5xx patches carry
across untouched, `llvm22-armhf.patch` is flagged as deleted upstream while
our `source=` still lists it, and the APKBUILD conflicts in exactly three
places -- the version, the source list, and `_gallium_drivers`. All three are
decisions, which is why step 4 is "stop".

The delta is already mostly a patch series — `temp/mesa` is three `.patch`
files plus a thin APKBUILD diff — which is why this works without adopting a
full quilt-style build system (§10).

## 8. `porthole pkg fork` records provenance

The root-cause fix. The primitive that creates a fork writes `upstream:`,
`forked:` and `commit:` into the manifest at fork time. Without this, every
future fork needs backfilling and §5 becomes a chore nobody does.

## 9. `porthole sync`

Repos come from config, never hardcoded: the working repo, porthole, and
pmaports.

- `porthole sync --out` — push what is committed in each. **Stops on a dirty
  tree, naming the files.** Whether that WIP is a commit or garbage is not a
  machine's call.
- `porthole sync --in` — fast-forward each. Refuses a diverged or dirty repo
  and says which.

It also reads the branch the **profile** expects, as
`PORTHOLE_PMAPORTS_BRANCH` in `device.env`.

**Built as a report, not an assertion** (2026-09-10). Measured before writing
it: pmaports on the reference host sits on `perf/crossdirect-native-link` --
not `edge`, and not the `taimen-bringup` that `porthole_cmd_channel.py`'s
comments still name. A feature branch there IS the normal working state, so
an assertion would fire on a healthy tree, and a check that fires on a healthy
tree is one people mute. The expected branch is printed beside the actual one
and never touches the exit code. The stale-runbook half of the justification
turned out to be already satisfied: there is no `BUILD-RUNBOOK.md`, and
`NEW-HOST.md` carries no branch name to rot.

`sync`'s actions are the positional `status`/`out`/`in` rather than the
`--out`/`--in` flags written below, because `tests/test_cli_rules.py` enforces
positional actions with `choices` across the whole CLI -- and because
`porthole sync status` is a prefix a permission rule can grant without also
granting `porthole sync out --yes`.

Never invents a commit, never rewrites history, never force-pushes. The failure
mode is "it stopped and told you".

## 10. Approaches rejected

**Git-native forks** — each fork a branch off the upstream aport, so rebase is
`git rebase`. pmaports is one repo holding every aport and Alpine's is another;
this needs per-package subtree or submodule gymnastics, and it fights
pmbootstrap, which wants files on disk.

**Full patch-series** — store only the delta against a pinned upstream and
generate the aport at build time. The best rebase story in theory, but it
changes how every fork is edited and how pmbootstrap sees them. Most of the
benefit is already available (§7), because our forks are already patch files.

## 11. Open questions

1. **`libcamera` tier.** Ours, and the device runs r3 against a fork at r18.
   Is that deliberate pinning because the camera is unfinished, or drift? If
   deliberate, it is `optional` with the reason recorded; if drift, `required`
   and the device is behind. Not a machine's call.
2. **Does `sync` cover the kernel tree?** ~~Recommendation: no, loudly.~~
   **Settled 2026-09-10: no, loudly.** `porthole sync` prints
   `kernel  not synced, by design` in every mode, and `kernel_tree` says the
   same in `--json`. Printed in every mode rather than only in `status`,
   because the mode where somebody assumes their kernel went with the rest is
   `out`.

## 12. Failure modes this must not introduce

| risk | mitigation |
|---|---|
| `sync` loses uncommitted work | refuses on dirty, names the files, changes nothing |
| `sync` creates a merge mess | fast-forward only; a diverged repo is reported, never merged |
| `rebase` corrupts a fork | scratch branch only; working branch untouched |
| manifest rots like the runbook did | `drift` reads it every run; a name with no aport fails `ph-pkgcheck.sh` |
| drift alarm gets ignored | `required` tier only by default; `optional` and unbuilt aports stay quiet, the lesson `pkg outdated` already learned |

## 13. Testing

- `drift` verdicts are pure version comparison — table-test apk ordering
  directly: `26.1.6-r14` vs `26.1.6-r0` (safe), vs `26.2.0-r0` (loses), vs
  `26.1.7-r0` (loses).
- Manifest parsing gets the same treatment `capabilities.conf` has.
- `sync` is tested against throwaway local clones: dirty, diverged,
  behind, and already-current, asserting it refuses the first two.
- Positive control, as ever: a test that cannot fail proves nothing — the
  drift suite must include a fork that **is** outranked and assert the alarm
  fires.

## 14. Sequencing

This is more than one implementation plan and should not be built as one. The
order is chosen so the silent-failure class closes first and the expensive,
judgement-heavy part comes last.

| plan | contents | why here |
|---|---|---|
| 1 | manifest (§5), `ph-pkgcheck.sh` reads it (§5), `pkg fork` records provenance (§8), `pkg drift` (§6) | closes the class that has already cost nine hours once. Useful the day it lands, and mesa stops being unguarded |
| 2 | `porthole sync` (§9) + `PORTHOLE_PMAPORTS_BRANCH` (§9) | independent of 1; unblocks working from the second PC |
| 3 | `pkg rebase` (§7) | needs 1's provenance to exist and be backfilled. The only part that can half-work by design, so it ships last and alone |

Plan 1 subsumes the earlier bounded proposal to widen `ph-pkgcheck.sh`'s watch
list; `mesa`, `libcamera` and `firmware-google-taimen` get covered there rather
than as separate work.

Plan 2 has no dependency on 1 and can be built first if moving between hosts is
the more pressing pain.
