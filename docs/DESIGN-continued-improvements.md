<!-- porthole | design | 2026-08-30 -->
<!-- porthole:design-doc -->
# Continued improvements: five themes from two ports

**Status: design, not yet built.** Commands below are proposals; the
`porthole:design-doc` marker above exempts them from
`tests/test_documented_commands.py`, which is right for a document describing
work that does not exist yet and wrong the moment it does. **Delete the marker
as each verb lands**, so the check starts holding these strings to the same
standard as the rest of the docs.

It lives in `docs/` rather than `docs/superpowers/` on the rule .gitignore
states: session artifacts record how the work was done, durable design belongs
in docs/. This is the second, and it should be deleted when its last theme
ships.

## Where this came from

Two independent sources, neither of them speculation:

- The **redfin (Pixel 5)** bring-up report, written after a session driven by a
  small local model. It reached 14/26 milestones and stopped at the first
  hardware boundary. Its value is the friction log: G1–G5, N1–N7, E1–E7, M1–M6.
- The **taimen** Epiphany and package-build handoffs, which carry measured
  numbers for build performance and progress reporting.

## Method: every claim was re-checked before it was planned

A plan built on an unverified report is a wishlist. Five of the reported items
turned out to be something other than what they looked like, and that changed
the design:

| reported | verified |
|---|---|
| G1 `aports lint` "not usable on 3.11.1" | the `lint` **subcommand no longer exists**, and porthole reports that tool failure as *"lint found problems"* |
| G3 `channel` "treated as edge" | `channel` is **not a config key** in 3.11.1 — the same root cause as G1, not a separate defect |
| G4 binfmt aarch64 not registered | **stale**; `porthole doctor` reports it registered. Was a NixOS host condition, not a porthole gap |
| E3 patch must be in `source=` | **`porthole aports patch` already does this**, including checksums. A discoverability failure, not a missing feature |
| crossdirect / ccache emulation | the kernel path **already solves the ccache half** (`_ph_arm_ccache` in `tools/ph-build.sh`); package builds never inherited it |

Exactly **two** of the seven pmbootstrap calls porthole makes have drifted
(`lint`, `config channel`). The other five are valid on 3.11.1. That bounds
theme A precisely instead of leaving it as "pmbootstrap compatibility".

Two more items were closed in prior sessions and are listed only so nobody
re-plans them: N1 (buildroot lock), N2 (`porthole pkg`), N3 (`builds` derived),
N5 (gcc-wrapper diagnosis).

---

## Theme A — a tool that breaks must not report a finding

**Problem.** `porthole aports lint` shells `pmbootstrap lint`, which 3.11.1
removed. pmbootstrap exits non-zero with `invalid choice: 'lint'`, and porthole
prints:

```
porthole: lint found problems
  → fix them before opening a merge request — pmaports CI runs this too
```

That is a false finding. AGENTS.md §6 makes this the cardinal sin — *"An agent
that cannot tell 'the tool broke' from 'the answer is no' reports broken tools
as findings"* — and porthole is committing it against itself. `porthole channel`
fails the same way more quietly: it asks for a config key that does not exist
and renders the error as `current: unknown`.

**Design.**

1. **A capability probe, not a version check.** Ask the installed pmbootstrap
   what it supports and cache it per pmbootstrap version. Version numbers lie
   across distro patches; the argparse choices list does not.
2. **An unavailable subcommand is `blocked`, never `failed`.** `lint` reports
   *"pmbootstrap 3.11.1 has no `lint`; it was removed upstream"* and exits with
   the code that means "could not run", not "found problems".
3. **`channel` reads the pmaports branch,** which is what actually determines
   the channel in 3.x, rather than a config key that no longer exists.
4. **A test locks the class shut.** Every pmbootstrap subcommand and config key
   porthole invokes is asserted to exist in the installed pmbootstrap. This is
   the same shape as `tests/test_documented_commands.py`: our dependency's API
   treated as code. It is the deliverable that stops theme A recurring, and it
   is why theme A is worth doing before the cosmetically larger themes.

**Boundary.** Not a compatibility layer, not vendoring pmbootstrap, no support
for multiple pmbootstrap versions. Detect, report honestly, and fail loudly in
CI when the surface moves again.

---

## Theme B — the sandbox is the build environment, so complete it

**Problem.** `dtc` is absent from the host *and* from the sandbox image, so
`porthole verify` ends `INCOMPLETE — 4 check(s) did not run` on every clean
checkout, and the device-tree check — *"the ONLY check that reads reg
properties"* — never runs for anybody. A verdict that is permanently incomplete
trains people to stop reading it.

**Design.** Add `dtc` to the sandbox Containerfile, and route `verify`'s
device-tree check through the sandbox when the host has no `dtc`.

Rationale for the image over the alternatives: the sandbox *is* the build
environment, so the toolchain belongs where the builds are. The image already
carries a Containerfile sha label and `_assert_lock_matches` refuses a stale
container, so existing workspaces are told to rebuild rather than silently
drifting. Routing through the container alone was rejected because `verify` is
specified to work with no device and no container running; the image gives a
complete default and the routing gives a fallback.

**G5, in the same theme.** The redfin host had no host pmbootstrap and the
`porthole build`/envkernel path assumes one. `porthole pkg` already routes to
the workspace, and `_workspace_usable()` is the existing decision point. The
work is to make the *kernel* path state plainly which pmbootstrap it will use
and why, and to fail with a fix rather than an obscure error when neither is
available.

**Boundary.** Not a general "install anything into the sandbox" mechanism. One
package, one rebuild, one clear reason.

---

## Theme C — bring-up ergonomics

**C1. `porthole slots probe` (N4).** The `slot-policy` milestone hint sends you
to `porthole new-device <codename> --from-fastboot`
(`lib/porthole_cmd_flash.py:43`) when the profile already exists — a
device-*creation* verb offered as the way to fill in a field. The redfin report
stopped exactly here.

A new verb reads `fastboot getvar all` and writes `HAS_AB_SLOTS`, the active and
forbidden slots, and the retry/boot state into the existing profile. A 27th verb
earns its place because slot policy is the one thing that must be probed before
any write to the device, and because the current answer is a dead end.

It must preserve the redfin session's best behaviour, called out in its own §M6:
**it never invents a value it cannot derive.** A field the device did not report
stays unset and is reported as unset. Guessing an active slot is how a port
bricks a phone.

**C2. Discoverability, which is the actual shape of E3 and N2.** Both "missing
features" existed. `porthole aports patch` already puts a patch into `source=`
and regenerates checksums; the report describes doing it by hand and losing time
to a checksum that was never generated. The fix is not a new feature but making
the existing one reachable at the moment of need — from `aports status` when it
sees an untracked `.patch` beside an APKBUILD, and from the failure path when a
checksum error appears.

**Boundary.** No general profile-editing UI. Two specific paths, both triggered
by a state porthole can already detect.

---

## Theme D — build performance

**D1. ccache runs emulated for package builds (planned).** Measured on taimen:
ccache is itself the aarch64 binary sitting in front of the compiler, so every
object pays emulated hashing of its preprocessed source. `_ph_arm_ccache()` in
`tools/ph-build.sh` already fixes exactly this for the kernel path by installing
ccache into `chroot_native` and making the compiler reachable through it. Package
builds never inherited it.

The design is to lift that pattern out of the kernel path so both use one
implementation. It is planned rather than spiked because the mechanism is
already proven in this repo against this workspace.

**D2. The crossdirect bypass (spike, not planned).** ~40% of compiler CPU is
still `qemu-aarch64`. crossdirect *is* working — cmake records the crossdirect
wrapper as `CMAKE_CXX_COMPILER` and native invocations are visible — but the
emulated half invokes the compiler **by absolute path** as
`qemu-aarch64-static /usr/bin/clang++`, bypassing PATH and therefore
crossdirect. What does that, and whether it is per-package or upstream, is not
yet isolated.

Writing an implementation plan against an unisolated cause produces a plan
against a guess. The spike answers one question — *what invokes the compiler by
absolute path* — and reports a recommendation. The claimed payoff (~2.4x on the
largest package in the tree) justifies the measurement; it does not justify
pretending the cause is known.

**Boundary.** No build-system rearchitecting. D1 is a port of an existing fix;
D2 produces a finding.

---

## Theme E — agentic workflow and impatient-developer DX

Requested explicitly, and grounded in defects observed this session rather than
in taste.

**E1. Two doors, one of them bad.** `porthole aports build` and `porthole pkg
build` both build packages. `aports build` calls raw pmbootstrap: no progress
bar, no `--lax` handling, no artifact verification, no `--detach`/`watch`. It
takes the buildroot lock only because that was added underneath it. Adding
`pkg` widened this gap rather than closing it, and the redfin developer used
neither. `aports build` should delegate to the `pkg` path so there is one
implementation and one behaviour.

**E2. `brief` does not say what is happening.** It reports device state and
traps but not: is a build running, who holds the buildroot, is another agent
working here. That is the first question when picking up a handoff, and this
session answered it by hand-rolling `podman exec ps` and lock probes. Two agents
collided in this repo today; a session brief that cannot say so is missing its
most load-bearing field.

**E3. Nothing reaps.** The container runs `sleep infinity` as PID 1
(`lib/porthole_cmd_sandbox.py:362`), which does not reap children. Four zombie
`clang++` processes were left behind by one interrupted build today, and a
long-lived workspace accumulates them. `podman run --init` exists for this.

**E4. Stopping a build is manual.** Cancelling a `--detach`ed build required
killing the host process and then the container-side pmbootstrap separately,
twice, by hand — and a half-killed build still holds the buildroot, which is
the exact state that destroys the next one. `porthole pkg stop` should do both
and release the lock.

**E5. Two progress implementations.** Package builds now compute an honest
windowed rate; kernel rungs still extrapolate from history. The kernel rungs
have real compile-line counts and could use the same arithmetic. One honest
implementation, not two.

**Boundary.** No TUI work, no new dashboards. Each item is a defect with a
reproduction from this session.

---

## Sequencing

1. **A** first — smallest, and it removes a source of false findings that would
   otherwise pollute every later verification.
2. **E1, E3, E4** next — they change how everything after them is exercised, and
   E4 in particular protects the buildroot the rest of the work depends on.
3. **B** — unblocks `verify`, which several later checks are read through.
4. **C** — independent; can land any time after A.
5. **D1** — after B, because it touches the same image and chroot plumbing.
6. **D2 spike** — last, and its output is a recommendation, not code.

**E2 and E5 are deliberately later**: E2 wants the lock and build state that E4
tidies, and E5 should not be attempted while the package arithmetic is still
settling.

## Success criteria

- No porthole verb reports a tool failure as a finding; a test asserts every
  pmbootstrap subcommand and config key porthole uses exists.
- `porthole verify` reaches a complete verdict on a clean checkout.
- `porthole slots probe` fills slot policy on an existing profile and leaves
  unprobeable fields unset.
- One package-build implementation, reachable from both verbs.
- `porthole brief` answers "what is running here" without a shell.
- A cancelled build leaves no process and no held lock.
- D1 lands with a before/after number on a real package build; D2 lands as a
  written finding.

## Explicitly out of scope

The device-side work in the Epiphany handoff — a5xx GPU faults, `MemoryHigh`
recalibration, the unexplained heat and power draw — and upstreaming the WebKit
V4L2 patch. Those are taimen bring-up, not porthole tooling, and they have their
own handoff.
