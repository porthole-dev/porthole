---
id: fastboot-boot-ignores-the-ramdisk-on-newer-pixels
title: On Pixel 7 and later, `fastboot boot` ignores the ramdisk — there is no RAM-boot safety net
scope: soc:gs201
subsystem: boot
severity: trap
confidence: probable
evidence: XDA "Has anyone figured out why fastboot boot ignores ramdisks?" (thread 4670620), reporting the change from the first Android-13-launch Pixel onward; AOSP "Build Pixel kernels" documents Pixel 7+ splitting vendor_boot into vendor_boot + vendor_kernel_boot. NOT yet confirmed on hardware — see the note at the end.
first-learned: 2026-08-24
---

Nearly every bring-up playbook, this one included, opens with the same advice:
boot a test image from RAM with `fastboot boot`, because it leaves the installed
system untouched and a bad kernel costs you one power cycle.

**That does not work on Pixel 7 and later.** Since the first Pixel that shipped
with Android 13, a boot image containing a ramdisk does not RAM-boot the way it
used to. The related platform change is that Pixel 7+ splits `vendor_boot` into
`vendor_boot` and `vendor_kernel_boot`, so the kernel artefacts no longer live
where the older flow expected them.

The consequence is not "an inconvenience". It is that **every test image is a
flash**, and that changes the shape of the whole bring-up:

- There is no cheap retry. A bad image is written to storage before you learn it
  is bad.
- The A/B slot policy stops being housekeeping and becomes the safety net.
  Establish `PORTHOLE_SLOT_FORBIDDEN` and keep a known-good image on the other
  slot **before the first write**, not after the first failure.
- A serial console matters more than usual, because you cannot iterate your way
  out of a boot that produces no output.

`tk-flash-boot.sh` already flashes rather than RAM-boots, so no tool needs
changing. What needed changing was the *documentation that promised a safety net
this device does not have* — the generated `new-device` checklist no longer
asserts it, and instead tells you to check
`porthole brain search --scope device:<codename>` before relying on it.

**Confidence is `probable`, not `proven`:** this is sourced from platform
documentation and community reports, not from a device in hand. Confirm it with
one `fastboot boot` of a known-good image before you trust either answer — and
if it turns out to RAM-boot fine, say so here and downgrade this note. An
untested trap that makes you *more* careful is cheap; the reverse is not.
