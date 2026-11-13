---
id: the-first-boot-lock-screen-pin-is-the-pmos-default-not-the-install-password
title: A freshly flashed taimen asks for a PIN on first boot, and the PIN is the postmarketOS default 147147
scope: device:google-taimen
subsystem: setup
severity: fact
confidence: probable
evidence: one first boot after a published-image install of a Pixel 2 XL, 2026-11-13; the candidate number was not recorded
first-learned: 2026-11-13
---

After a flash completes and the phone boots, the lock screen asks for a PIN.
The release `INSTALL.md` names the account (`user`) and says the initial
password is chosen by the maintainer and not published, so it is easy to read
the prompt as a password you were never given and stop there.

`147147` was accepted at that prompt. It is the generic postmarketOS default
PIN, so the image has not replaced it with a release-specific secret.

What this does not show: whether the account password for `user` (what ssh over
the USB gadget asks for) is the same value. Only the lock screen was tried.

Both are defaults. Change the PIN in the lock-screen settings and run `passwd`
as `user` before the phone is plugged into other machines.

Related: [[ssh-host-keys-change-every-boot]], [[device-google-taimen]].
