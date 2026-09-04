---
id: the-msm-reset-debugfs-does-not-make-a-client-lose-its-context
title: Writing msm's reset debugfs faults the GPU but no client loses its context, so it cannot test GPU-reset recovery
scope: soc:msm8998
subsystem: graphics
severity: trap
confidence: proven
evidence: two runs 2026-09-04, one with the compositor idle and one forced to repaint; gpu faults and "hangcheck recover!" in dmesg both times, phoc's "Re-creating renderer" count 0 -> 0 both times, screenshots before/after RMSE 0.008
first-learned: 2026-09-04
---

**Do not** use `/sys/kernel/debug/dri/<N>/reset` to reproduce a compositor's
GPU-reset recovery path. It looks like exactly the right knob and it is not.

    sudo sh -c 'echo 1 > /sys/kernel/debug/dri/128/reset'

does produce a real event -- a burst of `*** gpu fault:` lines, an
`a5xx_irq ... gpu fault ring 0`, and `recover_worker: hangcheck recover!`,
once even `adreno_idle: timeout waiting to drain ringbuffer` and
`hw init failed (-22), dropping submit`. What it does **not** produce is a
client-visible context loss. wlroots emits `wlr_renderer.events.lost` from
`render/gles2/pass.c` when `glGetGraphicsResetStatusKHR()` returns non-zero at
the end of a render pass; after this reset it keeps returning
`GL_NO_ERROR`, phoc never runs `recreate_renderer()`, and before and after
screenshots differ by RMSE 0.008 -- i.e. not at all.

Both arms were run: once with the compositor idle, and once trying to force
continuous repaints first. Same answer. (`DamageWhole` on
`mobi.phosh.Phoc.DebugControl` is not available either -- the bus name is not
activatable on a normal phoc, only on `phoc-dev`.)

**Instead**: the resets that do reach clients are the ones the driver takes on
its own under load -- 32 of them in seven days here, all under a browser with
Skia GPU rasterisation. Watch for them rather than trying to cause them:

    journalctl -b | grep -c "Re-creating renderer"

The reason this matters is that "the GPU was reset" and "a client lost its
context" are two different events, and only the second one exercises
[[wlroots-never-re-imports-a-texture-after-a-renderer-swap]]. An experiment
that causes the first and reports a null has tested nothing.
