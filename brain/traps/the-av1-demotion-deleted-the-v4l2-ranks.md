---
id: the-av1-demotion-deleted-the-v4l2-ranks
title: A second environment.d file setting the same variable deletes the first one's value
scope: generic
subsystem: media
severity: trap
confidence: proven
evidence: taimen 2026-08-30: device-google-taimen r36's 60-taimen-av1.conf silently dropped soc-qcom-gstreamer's v4l2 ranks
first-learned: 2026-08-30
---

`environment.d` has **no append**. A later file assigning a variable REPLACES
the earlier value outright, and nothing warns you -- not the package build, not
the boot, not the app that quietly loses the setting.

This bit a real device. `soc-qcom-gstreamer-systemd` ships
`/etc/environment.d/50-soc-qcom-gstreamer.conf`, promoting the hardware
decoders:

    GST_PLUGIN_FEATURE_RANK=v4l2vp8dec:SECONDARY,v4l2vp9dec:SECONDARY,
    v4l2h264dec:SECONDARY,v4l2h265dec:SECONDARY,v4l2mpeg2dec:SECONDARY,...

The device package then shipped `60-taimen-av1.conf` to demote AV1, which the
SoC cannot decode. 60 sorts after 50, so the running session had only:

    $ systemctl --user show-environment | grep GST
    GST_PLUGIN_FEATURE_RANK=dav1ddec:NONE,av1dec:NONE

Every v4l2 promotion was gone. The demotion existed *because* Venus decodes VP9
in hardware instead -- and it deleted the ranks that said so. The package was
working against itself, and the symptom is invisible: video still plays, just in
software, hot and slow.

**The fix is one line: reference the variable instead of replacing it.**

    GST_PLUGIN_FEATURE_RANK=${GST_PLUGIN_FEATURE_RANK},dav1ddec:NONE,av1dec:NONE

`environment.d(5)` expands `${VAR}` against what earlier files already set, so
this appends. If the other package is absent the reference expands to nothing
and the value gains a leading comma, which GStreamer tolerates -- checked with
`GST_PLUGIN_FEATURE_RANK=",dav1ddec:NONE" gst-inspect-1.0 dav1ddec`, which
reports rank none (0), identical to the value without it. No guard needed.

**Check it without a session restart.** The variable only reaches apps through
the user manager, so a plain `env` in an ssh session tells you nothing. Run the
generator directly:

    /usr/lib/systemd/user-environment-generators/30-systemd-environment-d-generator

It prints the merged result exactly as the manager will compute it, which is
also how you test a candidate fix before packaging it.

**Generalise this.** Before adding an `environment.d` drop-in, grep every
`environment.d` directory for the variable you are about to set:

    grep -rs '^YOUR_VAR=' /etc/environment.d /usr/lib/environment.d

If anything already sets it, reference it or you will delete it. The same holds
for any last-writer-wins drop-in directory -- this is not specific to
GStreamer, and [[shipped-configuration-is-not-running-configuration]] is the
law it belongs to: the file you shipped is not the value the process reads.
