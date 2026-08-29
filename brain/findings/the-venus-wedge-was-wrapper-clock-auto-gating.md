---
id: the-venus-wedge-was-wrapper-clock-auto-gating
title: The msm8998 venus wedge was wrapper clock auto-gating, and one write closes it
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-08-29, kernel 7.2.2 #15 (aport r14). Interactive /dev/mem probe with venus parked at stop_at=10, all clocks CCF-verified, all three GDSCs on. wrapper+0x04 reads 0x007f001f at power-on; a read of base+0x80124 wedges the SoC; after writing 0 to wrapper+0x04 the same read AND the preset write complete. Patch 0192 makes venus_run() do it; the full probe then completes, fw prints "venus hw 3.43.709 / VIDEO.VE.4.4-00051", /dev/video6+7 appear, decoder enumerates H264/VP8/VP9/HEVC.
refutes: the wedge is an XPU/write-protection story; TZ holding the venus CPU is what makes the block unreachable; a missing GDSC or CCF-visible clock explains the dead pages; the VBIF page is dead only to writes; the wedge needs a kernel-side bisect to localise further
first-learned: 2026-08-29
---

**The question** — [[venus-wedges-on-the-first-vbif-write]] closed everything
except: what makes VBIF reachable? Nine controlled refutations left "power,
clocks, TZ, ordering all innocent, one write still kills the SoC".

**The answer** — `WRAPPER_CLOCK_CONFIG` (venus base + 0xe0004) powers on as
`0x007f001f` on msm8998: internal clock auto-gating that leaves the VBIF page
(base+0x80xxx) and everything above wrapper+0x1000 unable to answer the config
bus. Any CPU access into those pages -- read or write, both measured -- stalls
the MMSS NoC until the watchdog fires, with nothing logged. Writing **0** to
that one register, which sits in the reachable low wrapper page, makes every
formerly-lethal access complete normally. The vendor zeroes it (plus
`WRAPPER_CPU_CLOCK_CONFIG`, wrapper+0x2000) in `__prepare_enable_clks()` on
every power-on, before its first venus register access -- see
`venus_hfi.c:3889` in ref/downstream-wahoo. msm8996 evidently powers on
permissive, which is why mainline never needed this and why the msm8998 RFC
was never proven on hardware.

Aport patch `0192-media-venus-disable-wrapper-clock-auto-gating-on-msm.patch`
(r14) writes both registers at the top of `venus_run()`, gated on IS_V3. With
it, the full probe completes on a clean boot: PAS auth, firmware boot,
SYS_INIT answered (3 IRQs), fw identifies as VIDEO.VE.4.4-00051,
`/dev/video6` (dec) and `/dev/video7` (enc) appear, all codecs enumerate.
This is upstreamable and worth sending to linux-media with the bisect story.

**What this rules out**

- *XPU / TZ write-protection.* A plain READ of 0x80124 wedges identically
  (run via /dev/mem with the probe parked at stop_at=10). Protection stories
  do not explain reads, and the unlock write needs no TZ involvement.
- *Any GDSC or CCF-visible clock.* At the moment reads wedged, MMCC hardware
  truth was read directly: video_top GDSCR on, both subcore GDSCRs claimed,
  core/iface/bus/mbus/mnoc_ahb branch CBCRs running per CCF's own halt polls,
  and every fabric clock (mnoc_maxi, bimc_smmu_*, vmem_*) hardware-on thanks
  to clk_ignore_unused. The gate was INSIDE the venus wrapper, invisible to
  CCF.
- *The of_platform_populate puzzle from the last session's task list.* Moot:
  with a genuinely successful core probe the video-decoder/video-encoder
  children populate and bind fine. The earlier "no children" observation was
  an artifact of boot_stage=7 fake-success probes.
- *TZ refusing resume as root cause.* Confirmed benign: -EINVAL happens only
  on the cold-boot resume (vendor never issues it there); after a real
  suspend, `scm_set_remote_state(resume=1)` returns 0 on this TZ.

**How it was established** — the parked probe (stop_at=10: fw loaded, PAS
done, zero MMIO, pm refcount held) turned the wedge from a boot-loop
experiment into an interactive /dev/mem session: one register per ssh, safe
reads first, one risky access as the cycle's last act. CONFIG_DEVMEM=y and
STRICT_DEVMEM unset on this kernel is what made that possible. Overturning it
would take showing 0x007f001f is not the reset value on some other boot path,
or that some arm's "all clocks on" state was misread -- both are one parked
probe away from a re-check.

**Still open, precisely characterised (the next campaign)**

**CLOSED the same evening** — all four are resolved or reframed in
[[venus-decode-works-and-what-it-took]]: (1) was the unclocked decoder
engine, not the event mechanism; (2)+(3) collapse into "the firmware does
not survive any power collapse", now sidestepped with pm_runtime_forbid;
(4) a pmaports temp fork of gst-plugins-good enables the stateful v4l2
element. The list below is kept as written for the record.

1. **First clean decode session stalls at source-change**: fw accepts
   SESSION_INIT and buffer queueing (bandwidth votes fire, ~13 IRQs), but no
   capture format ever becomes valid; ffmpeg's h264_v4l2m2m polls G_FMT x60
   and gives up with zero frames. Not yet split between "fw never raises
   EVENT_CHANGE (missing IMEM/VMEM SET_RESOURCE? fw 4.4 property?)" and
   "ffmpeg's stateful dance is inadequate for venus dyn-resolution".
2. **The first power collapse kills HFI**: any session opened after one
   runtime suspend/resume cycle gets ETIMEDOUT at REQBUFS (SESSION_INIT
   unanswered), even though the resume path re-runs venus_run and TZ resume
   returns 0. Pinning the core active (`echo on > .../power/control` BEFORE
   modprobe) avoids it. So does never suspending: fw messaging works on a
   clean never-suspended boot.
3. **An aborted session leaves the fw unresponsive** for every later session
   (same ETIMEDOUT), with one observed 100k/s venus IRQ storm in that state.
   One session per boot is the experimental discipline until this is fixed.
4. **Userspace has no working stateful client on this distro**: Alpine's
   gst-plugins-good ships only the stateless v4l2codecs plugin, and ffmpeg's
   v4l2m2m is the weak client above. GStreamer's v4l2h264dec (the reference
   stateful client) would need a rebuilt gst-plugins-good.

Test loop for the next person: modprobe venus_core after
`echo on > /sys/devices/platform/soc@0/cc00000.video-codec/power/control`,
then ONE ffmpeg session per boot; /etc/modprobe.d/00-venus-bringup.conf
carries the full explicit knob vector and the cmdline blacklist keeps
autoload off.
