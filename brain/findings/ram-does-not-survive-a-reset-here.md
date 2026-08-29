---
id: ram-does-not-survive-a-reset-here
title: No RAM survives a reset on taimen, so pstore/ramoops and ram_console are all dead ends
scope: device:google-taimen
subsystem: debug
severity: finding
confidence: proven
evidence: 2026-08-27: magic written via /dev/mem to 0xb01f0000, 0xb0e00000 and 0xaffff000, verified in-boot, all three DDR garbage after a reboot; deliberate sysrq-c panic also left /sys/fs/pstore empty
refutes: pstore is empty because nothing was written; enable PSTORE_FTRACE to catch the silent hang; move ramoops to the vendor's alt region; backport ram_console to get a persistent console; match downstream's ramoops address and it will work
first-learned: 2026-08-27
---
**The question** — a crash that prints nothing needs a witness that survives
the reset. netconsole cannot carry a silent hang and there is no serial cable.
pstore/ramoops is the standard answer, `PSTORE_RAM` and `PSTORE_CONSOLE` are
enabled, ramoops registers as a console at boot -- and `/sys/fs/pstore` is
always empty. Is something misconfigured?

**The answer** — no. **The RAM itself does not survive a reset on this device.**

Tested directly, with ramoops taken out of the picture entirely
(`CONFIG_STRICT_DEVMEM` is off, so `/dev/mem` can reach a `no-map` region):
write a magic to a physical address, read it back in the same boot, reboot,
read again. All three of the vendor's own regions, from
`ref/downstream-wahoo/.../lge/msm8998-taimen.dtsi`:

    main+0x1f0000  0xb01f0000  lost (f5fff9ffe7efd7df)
    alt +0x000000  0xb0e00000  lost (f7ff4ddff7dfda5f)
    meta           0xaffff000  lost (dffffffffd7edfd5)

Not zeroes -- DDR training garbage. The memory is re-initialised on the way
back up, not wiped by software.

Corroborating: a deliberate `echo c > /proc/sysrq-trigger`, which certainly
writes a dmesg record and console text to ramoops before resetting, also left
`/sys/fs/pstore` empty, with systemd-pstore's `ConditionDirectoryNotEmpty`
never firing and the ramoops driver registering with no prior records.

**What this rules out** — every RAM-backed persistence route, and the tempting
misreadings of the empty directory:

- **"Nothing was written."** A real panic was. Nothing survived it.
- **"Enable `PSTORE_FTRACE` and the silent hang will name itself."** It would
  be the right instrument -- it records function calls with no printing -- but
  it lands in the same memory. Note the DT has no `ftrace-size` either, so
  that buffer is not even allocated today.
- **"Move ramoops to the vendor's `alt_ramoops_region`."** Tested; the alt
  region does not survive either.
- **"Match downstream's address and size."** Already matched exactly
  (`0xb0000000`, `0x200000`) -- which is what the pmOS wiki tells you to do,
  and it is not sufficient here.
- **"Backport Android's `ram_console`."** The second half of that wiki page is
  a guide to it, but it is the same trick with an older driver against the
  same non-persistent RAM. Both its backport sections target pre-3.6
  downstream kernels anyway; taimen's vendor kernel is 4.4 and mainline is
  mainline, so there is nothing to backport in either direction.

**How it was established** — `a small /dev/mem poke script (not committed, since lost)`: `mmap` on `/dev/mem` at each
candidate physical address, write a 16-byte magic, verify in-boot, reboot,
verify again. What would overturn it: a boot path that leaves DDR trained --
this is a bootloader question (XBL/ABL), not a kernel one, and it is the only
thing that would make pstore viable here.

## 2026-08-27: what the vendor does, and what is actually closed

Re-examined against the vendor DT and the stock capture in `logs/`. The
headline above holds for the mainline configuration, but the reasoning needs
narrowing -- and one door is closed harder than before while another is not
closed at all.

**Closed, measured.** The kernel cannot enable download/ramdump mode on this
device. `qcom_scm` exposes `download_mode` as a writable module parameter, and
writing `full` to it produces:

    qcom_scm firmware:scm: No available mechanism for setting download mode

Both paths are unavailable: msm8998's DT has no IMEM dload child so
`dload_mode_addr` is NULL, and TZ does not offer `QCOM_SCM_BOOT_SET_DLOAD_MODE`.
The bootloader exposes no ramdump variable either -- `fastboot getvar all` has
`unlocked:yes` but `secure:yes` and nothing about dumps. So the preservation
flag really is in signed firmware, as predicted.

**Not closed, and worth knowing.** mainline's ramoops is registered correctly
and at the vendor's own address -- `ramoops: using 0x200000@0xb0000000`, which
is exactly `/reserved-memory/ramoops_region@b0000000` in the vendor DT -- and
it is an active console. Yet `/sys/fs/pstore` is empty after every reset,
meaning the `DBGC` zone signature was not valid at boot.

Meanwhile `logs/stock-console-ramoops.txt` is 350 KB of downstream 4.4 console
that **ends mid-shutdown** (`init: Untracked pid ... received signal 15`), which
cannot be captured live. The vendor kernel therefore did recover a previous
boot's console on this hardware.

The difference is not the address. The vendor uses a scheme mainline does not
implement: a **two-region ping-pong** -- `/soc/ramoops` has both
`memory-region` (`ramoops_region@b0000000`) and `alt-memory-region`
(`alt_ramoops_region@b0e00000`) -- plus a 4 KiB `ramoops_meta_region@affff000`
and a custom `access_ramoops` driver bound to each. That metadata page is the
only mechanism here that mainline has no equivalent of.

So the honest position: ramdump/download mode is dead behind signed firmware,
but "no RAM survives a reset" is a statement about mainline's plain single
region, not a property of the hardware. Whether the ping-pong is recoverable
without the bootloader's cooperation is untested. Timebox it the same way.
