---
id: the-session-is-back-to-30fps-on-7-2-and-ctl-start-is-not-why
title: The whole session is back to 30 fps on 7.2 -- the commit pipelining IS present, and the missing CTL_START patch is NOT why (msm8998 has no such interrupt)
scope: device:google-taimen
subsystem: display
severity: finding
confidence: proven
evidence: 2026-09-01, kernel 7.2.2 #25 aport r24. /sys/module/msm/parameters/ has no tail_pipeline, where docs/status/baseline-2026-08-08.txt line 119 recorded 'tail_pipeline Y' on the 6.x kernel. Camera video of the panel (30fps, 272 frames, 9.07s) shows 19.3 panel updates/s for 30fps content with freeze runs up to 334ms; 44 gst_base_sink_is_too_late warnings on a local H.264 clip decoding at 381% of realtime with GPU idle at 257MHz, psi_full 0.00 and no browser running.
refutes: the missing CTL_START pageflip patch is why 7.2 is slow; restoring 88149622b9e3 fixes the frame rate; YouTube stutter is a decode problem; the stutter is memory pressure; the stutter is a5xx GPU faults; the stutter is the weak wifi link; the stutter is VP9 vs H.264; the stutter is 24fps-on-60Hz judder; the stutter is WebKit
first-learned: 2026-09-01
---

**RESOLVED, same day.** The half-rate lock is fixed by TWO changes, both landed:
`2ec05b8d72f0` (send the pageflip at **rd_ptr**, the interrupt msm8998 actually
has -- CTL_START does not exist here) and **removing `FD_MESA_DEBUG=sysmem`
from the session**, which was costing the compositor the rest. Measured with
`profiles/google-taimen/tools/tk-framprobe.py`:

    baseline (7.2 as shipped)  31.4 fps  p50 30.5 ms   0.8% at 60 Hz
    + rd_ptr pageflip patch    39.5 fps  p50 22.4 ms  28.2% at 60 Hz
    + sysmem off               57.7 fps  p50 16.7 ms  97.6% at 60 Hz

That beats the historical best on record (17.7 ms, 78% at full rate). `sysmem`
was the corruption workaround from HANDOFF-2026-09-01 §2.3; the panel is clean
without it and only Epiphany needed it, which
`WEBKIT_SKIA_ENABLE_CPU_RENDERING=1` replaces. The residual browser stutter is
a different defect entirely -- see
[[waylandsink-delivers-5fps-where-other-sinks-do-60]].

**The question** — Epiphany, YouTube and even a local hardware-decoded clip all
stutter on taimen 7.2, and the phone runs hot. Which layer is at fault?

**The answer** — the whole **session** runs at ~30 fps on the 60 Hz panel, video
and UI alike. Measured with a GTK4 tick-callback probe, no video involved:

    31.4 fps over 8 s,  p50 interval 30.5 ms
    <20 ms  (60 Hz)        0.8%
    20-45 ms (30 Hz lock) 97.6%

The user independently confirmed it by scrolling the phosh navbar. This is the
same half-rate command-mode lock described in [[gpu-half-rate-and-a540-faults]],
whose historical signature was "rock-steady 33.4 ms frame callbacks".

**And the obvious suspect is NOT the cause.** `88149622b9e3` (send the pageflip
event at CTL_START) is on taimen-v6.0 and absent from taimen-v7.2, which looks
exactly like a lost fix. It is not:

- **msm8998 has no CTL_START interrupt.** Both trees carry the catalog comment:
  "Measured on a Pixel 2 XL: frames complete (PP_LINE_COUNT reaches vdisplay,
  DSI raises CMD_MDP_DONE) while INTR2 bits 9..13 never assert, so every
  ctl-start wait times out." A later commit, *drm/msm/dpu: msm8998: drop the
  nonexistent CTL_START interrupts*, removed `intr_start` for this SoC.
  `dpu_encoder_phys_cmd_ctl_start_irq()` is registered only when
  `phys_enc->irq[INTR_IDX_CTL_START]` is non-zero, so the handler never runs.
- **It was cherry-picked, ported to 7.2, built and flashed anyway** (two real
  6.0-isms: `data` -> `crtc`, and `parent_ops->handle_frame_done` ->
  `dpu_encoder_frame_done_callback`). Session frame rate after: **unchanged**.
  Reverted.
- **The memory note already said so** and was skimmed: "Kernel r13 (flip event
  at CTL_START) runs fine on the phone but **alone still 33.3 ms**". The
  17.7 ms came from r14, the pipelining patch. CTL_START never contributed here.

**What IS present on 7.2**: `4a19d30afcc3`, the pipelined commit tail.
`msm_atomic.c` carries "Deliberately no wait for the flush to complete here"
and has no post-kickoff `wait_flush`. Only its `tail_pipeline` module parameter
(from fixup `c9a269c49921`) is missing, so the skip is unconditional with no
off-switch -- which is why `/sys/module/msm/parameters/tail_pipeline` is absent
even though the behaviour is applied. **Do not read that missing parameter as
the missing fix.**

**So the question is still open**: the patch that delivered 60 fps on 6.0 is in
the 7.2 source, and 7.2 still measures 30 fps. Something else re-serializes the
commit path. That is where the next session should start -- with the tick probe,
not with video.

**What this rules out** — measured, each one innocent:

- **Decode.** venus does 1080p30 H.264 at 381% of realtime (VP9 at 118% is
  separately marginal and worth its own fix, but is not this).
- **Memory pressure.** Late buffers persist at `psi_full` 0.00, 2.1 GB free,
  browser stopped.
- **a5xx GPU faults.** All 10 in a boot were `SkiaGPUWorker`;
  `WEBKIT_SKIA_ENABLE_CPU_RENDERING=1` -> 0 faults, 0 ms at 710 MHz, half the
  GEM, `psi_full` 0.01. Stutter unchanged. Keep the flag anyway: it is the heat.
- **Network.** YouTube's own stats: 0 dropped of 3850, 46.8 Mbit/s, 56 s buffer.
- **Codec/ranks/AV1/sandbox.** `v4l2*dec` all primary+1; no AV1 threads;
  `/dev/video7` visible inside a web process that is not even in a mount
  namespace.
- **Decoder frame ordering.** 600/600 monotonic PTS on all three decoders.
- **24 fps-on-60 Hz judder.** A 30 fps local clip (exact 2:2) stutters the same.

**A second, independent defect worth its own fix** — a **SIGKILL mid-decode
wedges venus for the rest of the boot**: the node still answers ioctls and
advertises VP90, but no session yields a frame while software decode of the
same file succeeds. **systemd-oomd SIGKILLs Epiphany**, so this happens in
ordinary use. Recovery is a reboot.

**How it was established** — GTK4 tick-callback probe for the session rate; a
camera filming the panel for what is actually displayed (19.3 updates/s before
the patch, 22.6 after, on 30 fps content -- within the noise of two differently
framed clips, and not a fix); `gst_base_sink_is_too_late` counts; venus decode
benchmarks against `/proc/uptime`; `git merge-base --is-ancestor` plus
**content** greps, because a rebase renames every commit and SHA comparison
alone flags 204 false positives.

**Instrument traps paid for here, all mine.** `v4l2-ctl` opens its own fd and
reports the driver's default format, never another process's session. busybox
`sudo` refuses `-E`; busybox `date` has no `%N`. `gst-launch` only prints
`identity` messages with `-v`. A hand-built `matroskademux ! v4l2vp9dec` chain
lacks the queues `playbin` inserts. `porthole build` reported "make rebuilt
nothing" and **exit 0** on a compile error. A GTK4 `draw_func` raises
`cairo.Context` converter errors under this pygobject, silently measuring a
window that never painted -- damage via a CSS class swap instead. And three
`pkill -f` patterns matched the command line issuing them; the `[b]racket`
trick fails when the pattern also appears elsewhere in the same command, so
kill by recorded PID.
