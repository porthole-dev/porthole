---
id: venus-decode-works-and-what-it-took
title: Hardware video decode works on taimen -- three more root causes, and no power collapse
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-08-29 afternoon, kernel 7.2.2 r16-r20 (#17-#21). framemd5 bit-exact vs software decode for H.264 640x360 (three consecutive sessions with idle gaps) and VP9 720p; 1080p30 H.264 at ~95 fps wall / 0.43 s user CPU per 10 s clip. Cold boot to bit-exact decode with zero manual steps after r34 device pkg + modules-load.d. Each root cause has a mid-session /dev/mem register reading or an hfi_trace packet log behind it.
refutes: the fw stalls because ffmpeg's stateful dance is wrong; the missing IMEM/VMEM SET_RESOURCE is why sessions produce no frames; msm8998 venus power collapse can be made to work by rebooting the fw at resume; a failed session permanently poisons the firmware; the source-change event mechanism is broken on fw 4.4
first-learned: 2026-08-29
---

**The question** — after [[the-venus-wedge-was-wrapper-clock-auto-gating]], the probe
completed and /dev/video6+7 existed, but no session ever produced a frame.
Why, and what does 100% working decode take?

**Three more root causes, stacked** (aport patches 0194/0195/0196):

1. **The decoder engine was never clocked.** hfi_trace showed the fw
   answering the entire control plane (SYS_INIT, session init, LOAD_RES,
   START) and never completing one EMPTY_BUFFER; mid-session /dev/mem reads
   showed SUBCORE0 GDSCR powered while its CBCR had the enable bit clear.
   Patch 0190 had dropped `.vcodec_clks_num` to zero, turning every vcodec
   clock get/enable -- parent and per-session child -- into a silent no-op.
   0194 completes the msm8939 pattern: parent-held vcodec0/1_core clocks +
   vcodec_clks_num=2, so the engines are clocked from core power-on, before
   the firmware boots and probes them.

2. **The subcore GDSCs collapsed mid-setup.** With real clocks to enable,
   the mmcc HW_CTRL flag bit them: gdsc_enable() hands the domain to
   hardware immediately, nothing requests it, the domain collapses, and
   video_subcore0_clk trips "status stuck at 'off'" (-EBUSY), failing the
   probe. 0195 flips both subcore GDSCs to HW_CTRL_TRIGGER -- SW-on during
   setup, handed to hardware by venus's vcodec_domains_set_hw() after the
   clocks are up, the sdm845 videocc model.

3. **TZ threshold restore** (0196): Venus 3.43 needs TZBSP video state 2
   (RESTORE_THRESHOLD) after every firmware boot -- the vendor's documented
   handshake for TZ-owned registers that come out of a fw reboot incorrectly
   reset. Mainline never sends state 2.

**Power collapse is unfixable for now, so it is off** (0197 tried, 0198 is
the verdict): the firmware does not survive ANY power collapse on this SoC.

- TZ restore returns success and hands back an ARM9 that is out of WFI but
  ignores even a manually fired CPU_IC_SOFTINT (0xccdf018 = 0x8000, /dev/mem).
- A full reboot at resume -- shutdown, hfi_reinit, PAS re-auth, boot --
  passes the CTRL_INIT poll and then SYS_INIT gets silence.
- Even a driver unbind/rebind, the virgin cold-probe path, fails -EINVAL
  once the firmware has been shut down that boot.
- Clock rates (set_clk_rate=1), the wrapper unlock (verified still 0
  mid-hang), the threshold call, and every CCF-visible resource were each
  measured in place during the hang.

0198 does `pm_runtime_forbid()` on the venus core: powered at first use,
stays up while bound. First bring-up per boot is flawless and stable, so
this holds. Ceiling: idle power for VIDEO_TOP + clocks whenever venus is
loaded; upgrade path is the vendor's PC contract, via linux-media.

**Enablement, so apps see it**: device-google-taimen r34 drops the venus
modprobe.blacklist from the kernel cmdline (the kmod deny-list blocks even
explicit loads -- a modules-load.d entry alone is NOT enough), and
/etc/modules-load.d/venus.conf loads core+dec+enc at boot. The flashed
boot.img cmdline was patched with tools/bootimg-cmdline.py because the
export path rebakes only on kernel apk changes.

**Userspace status**: ffmpeg's h264_v4l2m2m and vp9_v4l2m2m work (they were
never the problem -- the G_FMT EINVAL loop was vdec_check_src_change()
correctly gating on a source-change the engine could never produce).
Alpine's gst-plugins-good ships NO stateful v4l2 element, so GStreamer
consumers (WebKit/Epiphany, GNOME apps) cannot use venus until the temp
fork (pmaports temp/gst-plugins-good, -Dv4l2=enabled) lands on the device.

**Instrument left in the tree**: `hfi_trace=1` on venus_core logs every HFI
packet both ways (0193). The knob vector from the wedge campaign remains;
/etc/modprobe.d/00-venus-bringup.conf now carries all-neutral values.
