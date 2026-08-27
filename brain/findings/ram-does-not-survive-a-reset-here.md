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
  6.18, so there is nothing to backport in either direction.

**How it was established** — `a small /dev/mem poke script (not committed, since lost)`: `mmap` on `/dev/mem` at each
candidate physical address, write a 16-byte magic, verify in-boot, reboot,
verify again. What would overturn it: a boot path that leaves DDR trained --
this is a bootloader question (XBL/ABL), not a kernel one, and it is the only
thing that would make pstore viable here.
