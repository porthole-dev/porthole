---
id: a-level-irq-with-a-pull-up-storms-when-its-chip-is-off
title: The NFC interrupt storm was a devicetree pull-up, not a driver bug
scope: device:google-taimen
subsystem: interrupts
severity: finding
confidence: proven
evidence: 2026-08-29, kernel 7.2.2 #3. /proc/interrupts irq 156 nxp-nci_i2c 26,176,487 counts in 27 min; sampled 6,560/s idle and 20,462/s with Epiphany running; /proc/583/stat gave the threaded handler 43,314 ticks = 433 s of CPU in ~1,950 s uptime. After 0180 flipped gpio92 to bias-pull-down, the live devicetree reads bias-pull-down and the same irq reads 0 on all eight CPUs.
refutes: the nxp-nci interrupt storm is a driver defect that needs a disable_irq fix; blacklisting the module is the fix; the storm explains the Epiphany jank; a spurious level interrupt shows up as a driver error in the log
first-learned: 2026-08-29
---

**The question** — the phone feels slow and the kernel ring buffer is full of
`handle_bad_irq` dumps for irq 13, the TLMM summary interrupt. What is
generating them?

**The answer** — `nfc_int_active` biases **gpio92 with `bias-pull-up`** while
the interrupt is declared `IRQ_TYPE_LEVEL_HIGH`. Mainline's `nxp-nci` holds VEN
low until userspace calls `dev_up()`, so through all normal operation the PN553
is unpowered and drives nothing, the pull-up holds the line high, and the level
interrupt is **asserted from `request_threaded_irq()` onwards**. The handler's
first I2C read NACKs (`NFC: Read failed with error -121`, EREMOTEIO, at t=10 s
on every boot), the driver latches `hard_fault` and from then on returns
`IRQ_HANDLED` without ever touching the chip -- so nothing ever deasserts the
line, forever.

Measured: **26,176,487 interrupts in 27 minutes**, 6.5k/s idle and 20.5k/s
under load, with the threaded handler burning **22% of a core continuously**
from boot. One line of devicetree took it to zero.

**The bias is not a typo, and copying the vendor is why it is here.** The
vendor's `msm8998-pinctrl.dtsi` really does pull this pin up, and downstream
that is harmless: `nq-nci` drives VEN high from its own probe, so the
controller owns the line and the bias only picks an idle level. The mainline
driver has the opposite power policy, which turns the same bias into a stuck
interrupt. **A vendor pinctrl value is only correct alongside the vendor's
driver.**

**What this rules out**

- *"It is a driver defect -- nxp-nci should `disable_irq_nosync()` when it
  latches `hard_fault`."* It arguably should, and that is still a real upstream
  robustness gap. But it is not the cause and fixing it there would only have
  hidden a devicetree error behind a driver workaround. The interrupt should
  never have asserted.
- *"Blacklist the module."* That was the first mitigation and it worked (6,560/s
  → 0/s), but it trades away NFC permanently to paper over one wrong property.
  With the bias fixed the driver probes normally, the irq is registered, and the
  count stays at 0 -- nothing had to be given up.
- *"The storm explains the Epiphany jank."* **It does not.** Fixing it changed
  nothing the user could feel. That symptom is a memory ceiling -- see
  [[epiphany-is-a-memory-ceiling-not-a-gpu-fault]]. Two real defects were live
  at once, and the loud one was not the reported one.
- *"A spurious interrupt will show up as a driver error."* Nothing in the log
  ever named NFC after t=10 s. What it produced was `handle_bad_irq` dumps
  against **irq 13** -- the TLMM chained parent, not the child -- and those
  dumps wrapped the kernel ring buffer, so `dmesg` had lost the entire boot by
  the time anyone looked. The instrument that found it was `/proc/interrupts`,
  which nothing had read.

**How to spot the class** — `sort -k2 -rn /proc/interrupts` on a phone that
feels slow. A level-triggered GPIO interrupt whose device is powered off is a
permanent storm, and it costs a core quietly: `top` showed 55% idle and 0% in
the `irq`/`sirq` columns while this was running at 20k/s.
