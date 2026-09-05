---
id: reading-the-qfprom-corrected-region-through-nvmem-hard-resets-the-phone
title: Reading the qfprom corrected region through nvmem hard resets the phone
scope: soc:msm8998
subsystem: nvmem
severity: trap
confidence: proven
evidence: "taimen 2026-09-03 20:50, kernel 7.2.2 #31. `sudo dd if=/sys/bus/nvmem/devices/qfprom0/nvmem bs=64 skip=256 count=64` (byte offset 0x4000 = physical 0x784000, the window downstream's gfx CPR node names fuse_base) never returned, the ssh session dropped, and the phone came back with uptime at 0 min and nothing in the journal. A python read of the same device from offset 0 a minute earlier also produced no output and no file. drivers/nvmem/qfprom.c serves the window with readb; downstream drivers/regulator/cpr3-util.c reads the same rows with readq_relaxed."
first-learned: 2026-09-03
---

**Symptom** -- you want the fused CPR voltages (row 65 of the corrected fuse
region carries the GFX rail's), you read
`/sys/bus/nvmem/devices/qfprom0/nvmem` as root, the read never returns, the
ssh session drops, and the phone reboots on its own. The journal has nothing:
the reset is below the kernel, and everything in `/tmp` (tmpfs) is gone with
it -- including any measurement captures kept there.

**Cause** -- the mainline qfprom driver reads its whole `reg` window one byte
at a time (`readb`), and the msm8998 corrected-fuse region at 0x784000 does
not take byte-wide accesses; the vendor CPR code only ever reads it as 64-bit
rows. The bus fault is taken by the secure watchdog, which resets the SoC.

**What to do** -- do not read that device. If the fuses are needed, add a
kernel-side 64-bit reader and try it only after the low-heat setup
(brightness, services, dump saver) is one script, so a reset costs a command
and not a session. Keep captures under `/var/tmp`, never `/tmp`. Why the
fuses matter: [[the-a540-gpu-rail-runs-below-its-cpr-ceiling]].
