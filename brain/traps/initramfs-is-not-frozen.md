---
id: initramfs-is-not-frozen
title: A device stopped in the initramfs looks exactly like a frozen one, and is nothing like it
scope: generic
subsystem: boot
severity: trap
confidence: proven
evidence: "taimen 2026-08-27: `tk_device_state` reported FROZEN; ping answered in 2 ms, ssh (22) and the rescue channel (2323) both refused, `fastboot devices` was empty. Port 23 was OPEN -- the pmOS initramfs debug shell -- and `tools/tsh.py` got `uname -r`, `/proc/cmdline` and the full `[pmOS-rd]` log out of it, which named the cause in one read: `losetup: /dev/sda13: failed to set up loop device` -> `ERROR: failed to mount subpartitions!` -> `Entering debug shell`."
first-learned: 2026-08-27
---

**Symptom** — the device pings, ssh is *refused* (not timed out), `fastboot
devices` is empty, and the state probe says `FROZEN`. Which sounds like the
kernel is up and userspace is dead, and sends you looking for a recovery tool
or a cable.

**Cause** — the boot stopped in the pmOS initramfs. It never got as far as
having a userspace to lose. The two states are genuinely indistinguishable on
the three probes anyone thinks to run -- ping, ssh, fastboot -- because in both
of them the kernel is answering and sshd is not.

The tell is **port 23**. The pmOS initramfs runs a busybox telnetd on it, and
nothing else on the device does. Connection *refused* rather than timed out on
22 is a second hint: something is answering RSTs, so the network stack is fine.

**What to do** — `porthole` now reports this as `INITRAMFS` rather than
`FROZEN`, and `brief`/`doctor` point at the tool. If you are on an older
checkout, probe port 23 by hand and then:

    tools/tsh.py "dmesg | grep 'pmOS-rd'"

That log states the reason outright. It is a far better place to be than
FROZEN implies: you have a root shell, `/proc/cmdline`, `dmesg`, the block
devices and the initramfs's own log, and `reboot -f` gets you out.

The most common reason to be there at all is a kernel that cannot load the
modules the initramfs needs -- see
[[a-fresh-kernel-cannot-ram-boot-against-installed-modules]].

Related: [[never-flash-a-tree-built-kernel-when-the-device-ships-from-an-aport]].
