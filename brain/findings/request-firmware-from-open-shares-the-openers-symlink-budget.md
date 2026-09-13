---
id: request-firmware-from-open-shares-the-openers-symlink-budget
title: request_firmware() from a file's ->open() shares that open()'s symlink budget, and a split firmware runs out
scope: generic
subsystem: kernel
severity: finding
confidence: proven
evidence: 2026-09-14, taimen, kernel 7.2.2 r46. fpc1020 loaded its trustlet (fpctzappfingerprint.mdt + b00..b07, nine files) on the first open() of /dev/fpc_tee. With pmOS's firmware_class.path=/lib/firmware/postmarketos (absent) and /lib -> usr/lib, the .mdt and b00..b06 loaded and b07 failed with -40 on every search path, including the absent one, which should be -ENOENT (dmesg "loading /lib/firmware/postmarketos/.../fpctzappfingerprint.b07 failed with error -40" through "/lib/firmware/..."). fs/namei.c __set_nameidata() starts a nested lookup at the outer walk's total_link_count and restore_nameidata() writes it back; MAXSYMLINKS is 40, and 8 files x 5 search paths x 1 symlink = 40. Loading from the first ioctl instead: all nine load with the same path setting.
refutes: /lib being a symlink breaks kernel_read_file_from_path_initns(); the firmware files or their directory contain a symlink loop; firmware_class.path must point at /usr/lib/firmware on a merged-/usr distro; ELOOP means a loop
first-learned: 2026-09-14
---

**Do not load firmware from a file's `->open()`.** `->open()` runs inside
the path walk that opened the node, and every lookup the firmware loader
makes from there is nested in that walk. `fs/namei.c` makes nested lookups
share one symlink count, to bound recursion: `__set_nameidata()` starts the
inner walk at the outer one's `total_link_count`, `restore_nameidata()`
copies it back. The limit, `MAXSYMLINKS`, is 40, and it covers everything
done during the one open().

The loader tries each search path in turn: `firmware_class.path`, the two
`/lib/firmware/updates` paths, `/lib/firmware/<release>`, `/lib/firmware`.
On a merged-/usr rootfs `/lib` is a symlink, so each attempt costs one
symlink, and a file found only in the last path costs five. pmOS sets
`firmware_class.path` to `/lib/firmware/postmarketos`, which usually does
not exist, so that is the usual cost. Eight files use up the budget, and
the ninth fails with `-ELOOP` on every path, including one that doesn't
exist and should have said `-ENOENT`. That is how to recognise it: the
first files of a split `.mdt` + `.bNN` image load fine and a later one
fails with -40 on every path. Drivers that load at probe time or from a
worker never see this, which is why "the same firmware directory works for
ath10k" proves nothing here.

Wrong fixes that appear to work: pointing `firmware_class.path` at
`/usr/lib/firmware` (the first path now succeeds with no symlink, but the
driver still fails for a user whose files live in `/lib/firmware/updates`),
or shipping fewer, larger files.

Fix: load from somewhere without an outer walk, such as the first ioctl,
probe, or a workqueue. linux-ws `fpc1020.c` loads its trustlet on the first
`FPC_TEE_IOC_XFER`.
