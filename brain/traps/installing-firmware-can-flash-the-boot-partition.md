---
id: installing-firmware-can-flash-the-boot-partition
title: apk add <firmware-pkg> (and apk fix) can FLASH the boot partition
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: taimen docs/DEVICE-PROTOCOL.md §6b; observed twice, 2026-08-19
first-learned: 2026-08-19
---

Files under `/lib/firmware` fire mkinitfs's apk trigger; mkinitfs calls
boot-deploy; and boot-deploy ends with a `dd` of a freshly built boot.img onto
the boot partition.

**So installing or repairing a firmware package is a flash**, in the middle of
whatever else is running. It is not a slot switch and it does not change which
kernel is installed — but an interrupted write leaves an unbootable slot, and it
is emphatically not what anyone typing `apk fix` on a phone expects.

```sh
apk add --no-scripts <firmware-pkg>     # files + ownership, no flash
```

On taimen nothing that package ships is read from the initramfs — modem, ath10k
and the GPU all load after switch-root — so skipping the trigger costs nothing.
Verify that for your device before adopting `--no-scripts` as a habit.
