---
id: the-bootloader-reboot-can-drop-the-phone-off-usb-entirely
title: The bootloader reboot can drop the phone off USB entirely
scope: device:google-taimen
subsystem: usb
severity: trap
confidence: proven
evidence: 2026-09-04 19:10, `porthole build fast`. tkflash-boot ran `reboot(RESTART2, bootloader)` through the PMIC reboot-mode register; the phone reached the bootloader screen and stayed there, and for ten minutes `lsusb` showed neither 18d1:4ee0 (fastboot) nor the d001 gadget. Unplugging and replugging the cable enumerated it immediately, in the same bootloader session. Seen once, on the run recorded in issue #59.
first-learned: 2026-09-04
---

**Symptom** — you asked for the bootloader, the phone went there, and then it
is not on the bus at all. `fastboot devices` is empty and stays empty;
`lsusb` shows **no 18d1:4ee0 and no d001**. Nothing enumerates, for as long as
you are willing to wait — ten minutes, in the one case recorded. The screen
says the phone is fine, which is what makes this read as a dead device rather
than a dead cable.

**Cause** — not established. What is established is where it is NOT: the phone
is up, the bootloader is running and it is drawing its screen. Only the USB
side is missing, and it comes back without the phone moving. So this is the
bootloader's gadget failing to come up on that boot, not a hang, not a bad
image and not the reboot being refused.

Do not spend the session on the theory. The observation that matters is that
it is recoverable in seconds and that waiting is not what recovers it.

**What to do** — **replug the cable.** It enumerated immediately, in the same
bootloader session, with nothing else changed. Then carry on; the device is
where you asked it to be.

Do not reboot out of the bootloader to "reset USB", and do not start a fresh
`porthole build fast` to get back to a known state. The rung resumes: a run
interrupted here has already pushed its modules, and `tkpush-modules` skips
the push and goes straight to the flash when it finds the device in FASTBOOT
with that exact module set recorded (issue #59). Starting over costs the four
minutes of build and the bootloader wait again, three times over in the run
this note comes from.

Related: [[a-refusal-is-not-a-hang]], [[busybox-reboot-eats-the-mode-string]].
