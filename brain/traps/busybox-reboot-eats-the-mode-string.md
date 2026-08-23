---
id: busybox-reboot-eats-the-mode-string
title: busybox `reboot bootloader` silently discards the word "bootloader"
scope: generic
subsystem: boot
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §1; porthole lib/porthole.sh tk_request_bootloader
first-learned: 2026-07-28
---

`sudo reboot bootloader` does not reach the bootloader on a pmOS rootfs, and
never did.

`/usr/sbin/reboot` is busybox. Its applet is `reboot [-d DELAY] [-nf]` — it
takes **no mode argument at all**, so the word `bootloader` is silently
discarded and you get an ordinary reboot. No error. No warning.

**Every apparent success is something else.** On taimen it was the A/B retry
counter running out on its own and dropping the device into the bootloader
anyway, which is why the command looked merely *flaky* for months rather than
broken.

The kernel side is usually already fine: a PMIC `pon` node with
`mode-bootloader` and a driver bound to it means the reboot-mode framework will
write the mode to the register the bootloader reads. What is missing is
userspace passing the mode string — `reboot(2)` with `LINUX_REBOOT_CMD_RESTART2`
rather than the plain `RB_AUTOBOOT` busybox issues.

porthole makes the syscall itself via python3 on the device:

```
142        = __NR_reboot on arm64 (generic syscall table)
0xfee1dead = LINUX_REBOOT_MAGIC1
0x28121969 = LINUX_REBOOT_MAGIC2
0xa1b2c3d4 = LINUX_REBOOT_CMD_RESTART2   (the one that carries a string)
```

~9 s to the bootloader, first try, and it does **not** burn a boot retry.

Set `PORTHOLE_REBOOT_MODE_VIA_SYSCALL=1` once you have confirmed the pon node
and its driver. Run it in the foreground and let the ssh connection die
mid-call — that death is the success signal. Backgrounding it races: ssh tears
the session down before the child reaches the syscall often enough that the
request is simply lost.

Related: [[ab-retry-counter-is-a-countdown-not-a-glitch]].
