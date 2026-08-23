---
id: 20-storage-usb-ssh
title: "Playbook: storage, USB and ssh"
scope: generic
subsystem: boot
severity: technique
confidence: proven
evidence: taimen README.md status; docs/BUILD-RUNBOOK.md
first-learned: 2026-07-25
---

**Goal:** a rootfs that mounts and a shell you can reach without hands.

**Done when:** `ssh` answers, survives a reboot, and `tk_boot_id` returns a value
that changes exactly once per boot.

## Order

1. **Storage first.** UFS/eMMC before anything else — without it there is no
   rootfs and every other question is unanswerable. Check the controller probed
   and the partitions enumerated.
2. **USB gadget.** The pmOS gadget gives you NCM networking plus ACM serial.
   This is the channel everything else depends on.
3. **ssh.** Then immediately do the one-time setup that makes the toolbox work:
   [[no-passwordless-sudo-disables-the-whole-toolbox]].
4. **Run `porthole doctor`.** It checks the above and names what is missing.

## The traps

- [[no-passwordless-sudo-disables-the-whole-toolbox]] — the "every tool is
  broken" report
- [[ssh-host-keys-change-every-boot]] — why the ssh flags are what they are, and
  the multiplexing rule that follows from it
- [[frozen-is-not-hung]] — ssh not answering is not the same as the device being
  down

## Measure your numbers now

Time a cold boot to first ssh, and a warm reboot cycle. Put them in the profile
as `PORTHOLE_REBOOT_BUDGET_S`. Until you have them, every boot verdict is a
guess ([[wait-long-enough-before-calling-a-boot-failed]]).
