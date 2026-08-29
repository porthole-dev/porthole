---
id: fresh-install-media-stack-is-self-sufficient
title: A from-scratch taimen install brings venus, GStreamer and the radios up with no hand-edits
scope: device:google-taimen
subsystem: build
severity: finding
confidence: proven
evidence: 2026-08-29 night, fresh pmbootstrap install (aport kernel r21, device r34, gst fork r1) flashed clean. First boot: /etc/modules-load.d/venus.conf, /etc/modprobe.d/*venus*, /etc/modprobe.d/*ipa* all ABSENT; venus_core loaded anyway; cc00000.video-codec modalias is of:...Cqcom,msm8998-venus. H.264 decode bit-exact (framemd5). v4l2h264dec/v4l2vp9dec/v4l2h264enc registered, libgstvideo4linux2.so owned by gst-plugins-good-1.28.5-r1. Modem 0 fatal errors, mmcli state registered. wlan0 up.
refutes: venus needs an /etc/modules-load.d entry to autoload; the ipa modprobe blacklist is needed for the modem; the modules-load.d and ipa hand-edits made during the radio incident are load-bearing; the gst v4l2 element is durable across Alpine upgrades
first-learned: 2026-08-29
---

**The question** — after a day of hand-edits on a running phone, what does
a genuine from-scratch install actually need to bring the media stack and
radios up? Which fixes are packaged, and which were scaffolding?

**The answer** — everything the user cares about is packaged; the
scaffolding was scaffolding.

- **venus autoloads with no config.** The platform device
  `cc00000.video-codec` carries modalias `of:...Cqcom,msm8998-venus`, which
  matches venus_core's alias table, so udev coldplug loads it. The
  `/etc/modules-load.d/venus.conf` added during the incident is NOT needed
  and was never packaged. What made autoload possible was device r34
  dropping the `modprobe.blacklist=venus_core,...` from the kernel cmdline
  (the kmod deny-list blocks even modalias autoload).
- **The ipa blacklist is not needed.** The 40 s modem crash loop was
  rmtfs-missing (see [[a-sideloaded-device-apk-can-eat-the-radio-stack]]),
  not ipa. On a healthy rootfs ipa autoloads and the modem registers with
  zero fatal errors. The `/etc/modprobe.d/10-ipa-blacklist.conf` from the
  incident was masking the real cause; do not package it.
- **Radios come up from the packaged `rmtfs`/`qcom-diag` device depends.**
  No manual daemon install on a clean rootfs.
- **Hardware decode reaches GStreamer** because the temp fork
  temp/gst-plugins-good (r1, -Dv4l2=enabled) is pulled transitively by the
  phosh UI and shadows Alpine's v4l2-less stock build.

**The one fragile piece** — the gst fork wins only while it and Alpine's
stock package share pkgver 1.28.5. The next Alpine bump silently outranks
it and hardware decode drops back to software with no error. Real fix:
enable v4l2 in Alpine upstream (draft at
taimen/vendor-patches/outgoing/alpine-gst-plugins-good-enable-v4l2.md),
then delete the fork. Documented in the fork's own README.

**The only genuinely manual step left is developer-only**: passwordless
sudo for the toolbox (`sudo -n`). It is deliberately NOT in the device
package -- shipping `%wheel NOPASSWD: ALL` to every installer is a
standing-root downgrade for what is a bring-up convenience. See
[[a-long-sudo-cache-is-unlimited-root]]. Keep it a per-developer setup
step, or gate it behind an explicit bring-up flag.
