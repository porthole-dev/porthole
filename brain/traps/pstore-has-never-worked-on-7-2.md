---
id: pstore-has-never-worked-on-7-2
title: pstore has never produced a record on 7.2.2, even though ramoops registers and enables a console
scope: device:google-taimen
subsystem: debug
severity: trap
confidence: proven
evidence: owner statement 2026-09-20 ('pstore never freaking worked in 7.2.2'), corroborated on kernel 7.2.2 #75 after a real watchdog reset: dmesg carries 'OF: reserved mem: 0xb0000000..0xb01fffff ramoops@b0000000', 'pstore: Registered ramoops as persistent store backend', 'ramoops: using 0x200000@0xb0000000, ecc: 0' and 'printk: legacy console [ramoops-1] enabled', and /sys/fs/pstore was still EMPTY -- systemd logged 'Platform Persistent Storage Archival skipped, unmet condition ConditionDirectoryNotEmpty=/sys/fs/pstore'
first-learned: 2026-09-20
---

**Symptom** — everything about pstore looks healthy, and there is never a
record. On 7.2.2 the reserved region is carved, the backend registers, and the
kernel even routes a console at it:

```
OF: reserved mem: 0x00000000b0000000..0x00000000b01fffff (2048 KiB) ramoops@b0000000
pstore: Registered ramoops as persistent store backend
ramoops: using 0x200000@0xb0000000, ecc: 0
printk: legacy console [ramoops-1] enabled
```

Then a real watchdog reset happens and `/sys/fs/pstore` is **empty**, with
systemd's own note for it:

```
Platform Persistent Storage Archival skipped, unmet condition
ConditionDirectoryNotEmpty=/sys/fs/pstore
```

**Why it matters more than it looks** — this is the instrument you reach for
the moment the phone hangs where no console can follow: a resume hang, a
suspend-side SoC stall, a panic before userspace. On 2026-09-20 an overnight
resume hang left nothing at all to read, and the reflex "check pstore, then fix
pstore" is a whole session with no output. **There is no log across a reset on
this kernel. Plan the investigation around that, not through it.**

The older note [[taimen-pstore-ecc]] records that the ramoops region can be
read out **from TWRP** and warns never to set `ecc-size`. That is about
recovering the region with another OS, and it is not the same thing as pstore
working from Linux -- which, on 7.2.2, it does not.

**Instead, for a hang with no console:**

- `/sys/power/pm_test` (`freezer` / `devices` / `platform` / `processors` /
  `core`) with `pm_print_times=1`. It runs the suspend and resume machinery and
  returns **without entering the real sleep state**, so the machine never goes
  down and `dmesg` survives. It is the only way to get a named, timed device
  list out of this failure class here. Its limit: nothing that requires the SoC
  to genuinely sleep -- the IPA interconnect stall, the RPM handshake, clock
  gating -- can reproduce under it.
- Binary bisect by runtime toggle, taking "did it come back" as the whole
  oracle. Slower, but it does not need a log.
- `CONFIG_PM_TRACE_RTC`, the x86 trick of hashing the last-resumed device into
  RTC scratch, is `depends on X86`. Not an option on arm64.

**Do not** spend the session making pstore work first.
