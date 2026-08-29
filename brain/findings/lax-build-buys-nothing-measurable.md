---
id: lax-build-buys-nothing-measurable
title: PORTHOLE_LAX_BUILD=1 saves no measurable time, and the zap it skips is not the wall clock
scope: generic
subsystem: build
severity: finding
confidence: proven
evidence: "Reference host 2026-08-29, warm buildroot, PMB_SUDO unset, one fresh bash process per run. `pmbootstrap build [--lax] --envkernel linux-postmarketos-qcom-msm8998-6.18`, interleaved nolax/lax/nolax/lax: 14.96 / 15.34 / 15.25 / 14.68 s. `pmbootstrap build [--lax] device-google-taimen`, same interleaving: 3.87 (cold) / 1.66 / 1.72 / 1.66 s. pmbootstrap 3.11.1 documents --lax as '(faster) do not zap chroots for faster package builds'."
refutes: "PORTHOLE_LAX_BUILD=1 is most of the wall clock in the flashing rungs; skipping the buildroot zap makes iteration meaningfully faster; the flag is a good default while iterating"
first-learned: 2026-08-29
---

> **CORRECTED 2026-08-29, evening.** The conclusion below holds; half its
> evidence does not. The kernel row of the table -- 14.96 / 15.34 / 15.25 /
> 14.68 s, presented here as the strongest reading because it showed no
> difference "at all" -- could not have shown one. `pmbootstrap build
> --envkernel` returns from `pmb/commands/build.py` (3.11.1, lines 15-17)
> BEFORE the strict-mode zap block, so `--lax` never reaches that path. Four
> readings of a flag that was inert by construction.
>
> The device-package row is the live measurement and it stands: 1.66-1.72 s
> either way, warm, interleaved. So the verdict is unchanged and the reason is
> now narrower and firmer -- on the envkernel rung the flag does nothing at
> all, and on the package rungs it saves 60 ms.
>
> One thing the name hides, found the same evening: `zap_buildroots()` deletes
> **chroot_native** as well as `chroot_buildroot*` (`pmb/chroot/zap.py:48-50`).
> A non-lax package build therefore takes the toolchain the next envkernel
> compile would have used with it, which is why a "chroot re-init" surfaces as
> a full kernel rebuild rather than as a chroot message. See
> [[envkernel-disables-ccache]].

> **CORRECTED AGAIN 2026-08-30, and this one inverts the advice for the
> workspace.** Everything below was measured ON THE HOST. In the rootless
> workspace a non-lax `pmbootstrap build` does not merely cost nothing -- it
> **cannot run**. `zap_buildroots()` umounts the chroot, and the recursive
> `/dev` bind the workspace needs puts propagated sub-mounts in
> `/proc/self/mountinfo` that a rootless userns cannot umount by path:
>
> ```
> umount: /pmb/chroot_native/dev/shm: not mounted.   (exit 32)
> ```
>
> Discriminated by running each once and slicing pmbootstrap's log: nolax
> reaches "Zapping buildroots" and dies there, before building anything;
> `--lax` skips the zap and fails later at an unrelated point. So in the
> workspace `--lax` is the only path that gets past the zap, and "do not reach
> for it" is HOST advice. [[what-a-rootless-workspace-cannot-do]] §5.
>
> **Upstream's reason for the default, since asked** -- `e14f4169`, Aelin,
> 2026-05-23, MR 2939: *"Strict mode is the more correct one and results in
> consistent, reproducible behavior, which is why I believe it should be the
> default. If users want quick builds without setting up the buildroot several
> times, --lax can now be used."* Correctness, explicitly. That is the same
> hazard this note weighs, now in upstream's own words rather than ours.
>
> Note how NEW all of this is: `--lax`, strict-as-default and
> `zap_buildroots()` all arrived in that one 2026-05 commit. Before it, lax was
> the default and no build ever deleted `chroot_native`.
>
> **Still not re-measured, and honestly:** the device-package A/B below was
> inherited, not re-run. It could not be re-run here -- a second, independent
> blocker (`gcc-x86_64`/`g++-x86_64` "no such package": the local x86_64 cross
> toolchain has never been built in this workspace) stops package rungs on BOTH
> paths, lax included. Cause undetermined. The host numbers stand as host
> numbers.

**The question** — `ph-build.sh` and `AGENTS.md` both say `pmbootstrap build`
zaps the buildroot before every package and that this "is most of the wall
clock in the flashing rungs", with `PORTHOLE_LAX_BUILD=1` offered as the
knowing trade. It had never been measured here. How much does it buy?

**Nothing measurable.** Interleaved runs, warm buildroot:

| package | nolax | lax | nolax | lax |
|---|---|---|---|---|
| kernel (`--envkernel`) | 14.96 s | 15.34 s | 15.25 s | 14.68 s |
| device package | 3.87 s (cold) | 1.66 s | 1.72 s | 1.66 s |

The kernel package shows no difference at all — lax is *slower* in two of four
readings, which is noise. The device package is 1.7 s either way once warm; the
one 3.87 s reading is the first run of the sequence.

**What this rules out** — that the zap is where the flashing rungs spend their
minutes. It is not. Those minutes are `install`, `export`, the flash itself and
the boot wait, none of which `--lax` touches.

**So the flag is a bad trade**: it accepts a documented, repeatedly-paid
hazard — stale build state presenting as a mysterious wrong-kernel bug — in
exchange for no measured gain. Do not reach for it while iterating. Reach for
the right rung instead, which is worth minutes rather than seconds.

**What would still overturn this**: a genuinely cold buildroot, where the zap
has real work to skip. Every reading above is a rebuild of a package that was
already built, which is exactly the iterating case the flag is recommended for
— so the recommendation is refuted on its own ground. If a from-scratch
dependency set is ever timed both ways, record it here.
