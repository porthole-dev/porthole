---
id: 15-vendor-blobs
title: "Playbook: getting at vendor firmware"
scope: generic
subsystem: build
severity: technique
confidence: proven
evidence: taimen docs/BLOBS.md (verified against the RP1A.201005.004.a1 factory image); porthole lib/porthole_cmd_blobs.py
first-learned: 2026-07-25
---

**Goal:** know what firmware the vendor image contains, which mainline driver
consumes each blob, and where it expects to find it.

**Done when:** you can name every blob a subsystem needs before you try to
bring that subsystem up.

## Two different questions

**For shipping**, postmarketOS packages firmware in a `firmware-<device>` aport
that fetches from a source pmaports accepts. That is settled policy and not
what this playbook is about.

**For development**, you need to know what is in the image *before* you can
write that aport at all. Which blobs exist, what they are called, what consumes
them. That is this.

## Read without mounting, and without root

```sh
porthole blobs unpack factory.zip --list    # a factory image NESTS
porthole blobs unsparse vendor.img          # Android sparse -> raw
porthole blobs ls vendor.raw.img            # ext4, via debugfs, no mount
porthole blobs inventory vendor.raw.img     # what consumes what
```

Two things make this pleasant instead of a fight:

- **`debugfs -R "ls -l /firmware" image` reads ext4 without mounting it.** No
  loop device, no root. A loop mount needs privilege, and privilege is what
  this project spends its time avoiding — see
  [[a-long-sudo-cache-is-unlimited-root]].
- **Android sparse images decode from their own header**, so `simg2img` is not
  required. The format is a 28-byte header and four chunk types.

## What the file names tell you

The names repeat across Qualcomm devices, which is what makes this knowledge
worth carrying rather than re-deriving:

| blob | consumed by | where it goes |
|---|---|---|
| `wlanmdsp.mbn` | `ath10k_snoc` | `ath10k/WCN3990/hw1.0/` |
| `bdwlan.bin`, `bdwlan.b??` | `ath10k_snoc` | board data — see below |
| `a???_zap.mdt` | `msm/adreno` | the GPU stays in secure mode without it |
| `a???_gpmu.fw2` | `msm/adreno` | the GPU will not clock without it |
| `adsp.mdt` | `q6v5_pas` | no audio without it |
| `slpi*.mdt` | `q6v5_pas` | accel, gyro, proximity route through it |
| `venus.mbn` | `venus` | hardware codecs; software ones work regardless |
| `*.jsn` | `pd-mapper` | protection domain maps |

## Four things that cost time

**The factory image nests.** Outer zip → `image-*.zip` → the partitions. People
lose an afternoon to the structure alone.

**`board-2.bin` and `firmware-5.bin` are GENERATED, not shipped.** They are
built from `bdwlan.bin` + `bdwlan.b??` with `qca-swiss-army-knife`. Searching
vendor.img for them finds nothing, which reads as "the image is incomplete".

**Modem firmware is not in `vendor.img`.** It lives on its own `modem_a` /
`modem_b` partition, readable read-only from the running device once storage is
up — no recovery image needed. Only ever *read* those partitions, and back up
`modemst1`/`modemst2`/`fsg`/`fsc` before anything that writes.

**pd-mapper needs two maps that live in different images.** `modemr.jsn` is on
the modem partition; `modemuw.jsn` is in `vendor.img`. The second declares the
`wlan/fw` service that `ath10k_snoc` blocks on, so with only the first,
pd-mapper exits "no pd maps available" and WiFi never comes up. And pd-mapper
looks for them *next to the firmware*, honouring `firmware_class.path`, not in
`/lib/firmware`.

## Size the reserved memory from the blob itself

A remoteproc blob's own program headers say how much memory it needs. Taimen's
`modem.mdt` spans `0x8cc00000..0x94400000` — 120 MiB — while stock reserved
112 MiB, so the mainline override was required rather than cosmetic. **Read it
out of the blob rather than copying a number from downstream.**

Related: [[50-wifi-bt-modem]], [[running-a-device-script-on-the-host]],
[[instrument-guilty-until-proven-innocent]].
