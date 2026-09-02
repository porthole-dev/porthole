---
id: a-venus-firmware-assert-wedges-the-gdsc-and-recovery-spins-forever
title: A venus firmware assert wedges the video GDSC, and the driver's recovery then retries every 10 ms forever
scope: soc:msm8998
subsystem: media
severity: finding
confidence: proven
evidence: "taimen 2026-09-02, kernel #28 (7.2.2). Decoder firmware asserted mid-playback at 19:52:40: `no valid instance(pkt session_id:dead, pkt:21001)`, `dec: event session error 0`, `SFR message from FW: QC_IMAGE_VERSION_STRING=VIDEO.VE.4.4-00051 Err_Fatal - Z:\\b\\venus_proc\\venus\\utils\\src\\vbuffer.c:569:`. 610 `System error has occurred, recovery failed to init HFI` lines over the following ~15 minutes. rmmod/modprobe of venus_core then failed: WARN in gdsc_toggle_logic -> gdsc_enable -> genpd_power_on -> venus_runtime_resume -> venus_probe, `probe with driver qcom-venus failed with error -110`. /dev/video0-5 gone, v4l2vp9dec absent from the GStreamer registry. A reboot restored everything (all of /dev/video0-7, zero system-error lines)."
refutes: "the venus recovery path gives up on its own; a decoder that dies only costs you hardware decode; the recovery warning rate reflects the retry rate"
first-learned: 2026-09-02
---

**The question** — the decoder stops working mid-session and dmesg fills with
`System error has occurred, recovery failed to init HFI`. What state is the
machine actually in, and does it come back?

**The answer** — it does not come back without a reboot, and while it is not
coming back it spins.

**1. The firmware asserts.** `Err_Fatal ... vbuffer.c:569` in the SFR (sub
system failure reason) region, session id `dead`. Seen here during a
**mid-stream resolution change** on 1440p60 VP9, which is what YouTube's
adaptive switching does unprompted.

**2. The video GDSC will not power on afterwards.** Recovery calls
`venus_boot()` -> `pm_runtime_get_sync()` -> `genpd_power_on()` ->
`gdsc_enable()` -> `gdsc_toggle_logic()`, which times out (`-ETIMEDOUT`).
A full `rmmod venus_dec venus_enc venus_core; modprobe venus_core` hits the
same timeout in `venus_probe`, so the driver cannot re-take the hardware.
`/dev/video0-5` disappear and `v4l2vp9dec` vanishes from GStreamer's registry
-- **everything silently falls back to software decode from that moment on**,
which is a trap for any measurement running across it.

**3. The recovery handler never gives up.** `venus_sys_error_handler()`
(drivers/media/platform/qcom/venus/core.c) ends with:

```c
if (failed) {
        disable_irq_nosync(core->irq);
        dev_warn_ratelimited(core->dev,
                             "System error has occurred, recovery failed to %s\n",
                             err_msg);
        schedule_delayed_work(&core->work, msecs_to_jiffies(10));
        return;
}
```

No attempt counter, no backoff. Each pass runs `pm_runtime_get_sync()`,
`core_deinit()`, `venus_shutdown()`, `hfi_reinit()`, `venus_boot()` and
`hfi_core_resume()`, several of which sit out their own timeouts first.

**The log lies about the rate.** The message is `_ratelimited`, so it prints
about once a second while the work re-runs every 10 ms plus timeout time. Read
the interval between those lines as a retry rate and you will conclude the
machine is idle; it is not. `irq/158-venus` was taking measurable CPU in `top`
the whole time.

**Fixed** by aport patch `0209-media-venus-bound-the-system-error-recovery-retries.patch`
(pkgrel 29, kernel #30): count consecutive failures, back off 10/20/40/80 ms,
give up after five. `core->sys_error` stays set on give-up, which every HFI
entry point in hfi.c already tests, so calls fail cleanly instead of hanging,
and nothing waits on `sys_err_done`. The hardware still needs a reboot -- the
patch stops the machine burning CPU until you give it one.

**What to check before trusting any media measurement**:

```sh
sudo dmesg | grep -c "System error has occurred"   # must be 0
ls /dev/video*                                      # 0-7 present
gst-inspect-1.0 v4l2vp9dec >/dev/null && echo hw decode present
```

Related: [[the-sigkill-venus-wedge-was-vp9-bandwidth-starvation]] -- a
different, and recoverable, way for venus to look dead.
