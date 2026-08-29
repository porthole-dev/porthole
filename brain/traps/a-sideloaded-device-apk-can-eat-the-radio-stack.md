---
id: a-sideloaded-device-apk-can-eat-the-radio-stack
title: A sideloaded device apk can eat the radio stack
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-08-29 taimen. "apk add --allow-untrusted /tmp/device-google-taimen-0.1-r34.apk" over ssh dropped the installed package count 1238 -> 1205 in one transaction; /usr/bin/rmtfs vanished while "apk info rmtfs" still answered with a description and an empty -L; "apk audit --system" later showed unrelated /usr/sbin binaries (rfkill, vipw, suexec, xfs_io) deleted. Modem entered a metronomic 40 s "fatal error without message" loop (boots without EFS), and wifi died with it because the WLAN firmware runs as a PD on the modem DSP. Radios died on every boot after the transaction regardless of any kernel change -- including with the new driver fully blacklisted, which is what exonerated the kernel.
first-learned: 2026-08-29
---

**Symptom** — after upgrading the device package from a locally built apk,
the modem takes a "fatal error without message" exactly every 40 seconds,
forever, and wifi never brings its firmware up. The bring-up unit logs
"rmtfs never opened qcom_rmtfs_mem; modem will boot without EFS". apk
claims everything is installed and "apk add -s" says the world is
consistent.

**Cause** — the sideload transaction quietly removed a dependency subtree
(and left husk database entries: package queryable, file list empty). On
Qualcomm phones the radio userspace is exactly such a subtree: rmtfs,
tqftpserv, diag-router, pd-mapper. No rmtfs means the modem boots without
EFS and its storage watchdog kills it on a 40 s period; the WLAN firmware
often runs as a PD on the modem DSP, so wifi dies with it. The damage can
extend further than the removed subtree -- audit showed unrelated sbin
binaries gone -- so do not trust the rootfs after this.

**What to do** —
- BEFORE sideloading a device apk: note `apk info | wc -l`, and diff it
  after. A drop is packages being removed under you; abort and read the
  full transaction output (never tail -1 an apk transaction).
- To confirm the class of breakage: `apk audit --system` lists deleted
  files (lowercase `x` lines) that the db still expects.
- Surgical repair works for the radio set (push rmtfs/tqftpserv/
  qcom-diag/pd-mapper apks from the host cache, canonical names + `apk
  index --allow-untrusted`, extract the -systemd units by hand if the
  subpackages refuse) -- wifi returns within seconds of rmtfs+diag-router
  serving. But if the audit shows unrelated deletions, reflash the rootfs
  instead of trusting the repair.
- The kernel is exonerated the moment the symptom survives with the new
  driver blacklisted. Run that control FIRST, before blaming the change
  you just made -- a day's kernel work made the kernel the obvious
  suspect, and it was innocent.
