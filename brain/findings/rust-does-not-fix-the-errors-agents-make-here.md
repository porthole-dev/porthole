---
id: rust-does-not-fix-the-errors-agents-make-here
title: Rewriting porthole, pmbootstrap or the msm8998 drivers in Rust does not address the class of error this port actually hits
scope: generic
subsystem: process
severity: finding
confidence: proven
evidence: "Every bug this port lost a session to, classified 2026-09-18 from brain/findings and brain/traps: a DT capacity value (unpinned boot 22.68s -> 4.65s); the taimen fps instrument reading `encoder31`, a 6.0 object id, on 6.18, so every fps number before 2026-08-10 was a silent zero; dsi.xml.h offsets 0x50-0x90/0x1b0-0x1c0 wrong on 8998 with 0x0E readback lying; MSM8998 having no CTL_START IRQ while the catalog bits say otherwise (sdm845 fiction); missing DPU<->DSI frame serialization; plain memset() on the MSA carveout, which is Device memory, panicking via `dc zva`; CONFIG_QCOM_QFPROM renamed to CONFIG_NVMEM_QCOM_QFPROM and dropped by olddefconfig, killing all USB; CONFIG_ZRAM_BACKEND_LZ4 unset so zram silently fell back to lzo-rle; GSI WRR_WEIGHT=0 starving the modem TX channel. Count of these that are memory-safety or type-safety defects: zero. Rust-for-Linux as of 6.18 has no bindings for drm/msm, clk, iommu, remoteproc or DSI."
refutes: "rewriting porthole in Rust would reduce agent bugs; rewriting pmbootstrap in Rust would reduce agent bugs or is what frees us from Alpine; converting msm8998 drivers to Rust is feasible and would cut agent errors; a stricter language is the lever on agent error rate here"
first-learned: 2026-09-18
---

The question keeps coming back because the premise is true: agents do make a
lot of errors here. The conclusion does not follow, and this note exists so
the next person searching for "rewrite in Rust" finds the measurement instead
of re-deriving the argument.

## The errors are wrong-value and wrong-model, not wrong-memory

Classify every bug in `brain/` that cost this port a session. The evidence
line above lists them. They fall into three kinds:

1. **A value that was wrong** — a DT capacity, `WRR_WEIGHT=0`, a Kconfig
   symbol silently renamed out of the defconfig.
2. **A model of the hardware that was wrong** — `dsi.xml.h` offsets that do
   not apply to 8998, a `CTL_START` IRQ that does not exist on this SoC,
   a carveout assumed to be Normal memory when it is Device memory.
3. **An instrument that was not measuring** — the fps tool reading a 6.0
   object id on a 6.18 kernel and returning a confident zero.

**Not one is a memory-safety or type-safety defect.** Rust's guarantees do not
reach any of the three. A `u32` register offset is a `u32` whether or not the
compiler checked the borrow; `memset` vs `memset_io` is a property of the
mapping, not of the type; and kind 3 is the failure mode
`brain/laws/every-test-needs-a-positive-control.md` already exists to catch.

What *does* reduce this error rate is already in this repository and
underused: a `refutes:` line that makes a dead theory findable by the theory
rather than by the conclusion, and deciding the positive control before the
run. That is where the effort goes.

## porthole

Subprocess orchestration and text parsing. Rust's guarantees do not apply to
`pmbootstrap` exiting 78, to a `fastboot` that hangs, or to a DTS property
that reads back as something else.

It would also make the loop worse. `porthole build mod` is a ~40 s rung
(`brain/findings/the-workspace-loop-is-seconds-and-still-uncached.md`), and
inserting a compile of the toolbox itself into the path an agent iterates on
is a direct regression to the thing that was measured and tuned.

## pmbootstrap

The distro-freedom argument is the strongest reason **against**, not for.

Mainline does not force Alpine — you can build a Debian or Arch ARM image from
the Python that exists today. The language is orthogonal to that question.
What a rewrite actually buys is owning a distro build system forever: aports,
the binary repository, the cross toolchain, the device packaging, the signing
chain on `pmos-packages`. postmarketOS's value here is that ecosystem, not its
implementation language.

And the expensive part today is already divergence from upstream —
`docs/SANDBOX.md` is carrying an unreleased cross-native2 patch, applied with
`git` because the image has no `patch(1)`. A rewrite makes that divergence
total and permanent.

## The drivers

Not feasible, independently of whether it would help.

- Rust-for-Linux has no bindings for `drm/msm`, clk, iommu, remoteproc or
  DSI. The abstractions would have to be written and upstreamed first, before
  the first line of driver.
- Nothing upstream accepts an out-of-tree Rust rewrite of `drm/msm`. This
  port's whole strategy is to stay close enough to mainline to carry patches
  (`brain/findings/` on the msm8998-mainline fork); a rewrite is the opposite
  of that.

## What is worth keeping in Rust

Userspace that is its own program. `obscura` is GTK 4 + libcamera in Rust and
should stay that way — different problem, and the choice was already right.
This note is about the kernel, the toolbox and the distro tooling.
