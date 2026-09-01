---
id: panel-corruption-was-gpu-reset-wreckage-not-tearing
title: The panel corruption and degraded phosh were one GPU reset's wreckage -- not display tearing, and not the rd_ptr patch
scope: device:google-taimen
subsystem: display
severity: finding
confidence: proven
evidence: 2026-09-01 ~20:17, dmesg t=2605s "gpu fault ring 0 status D301B1C1" + "hangcheck recover!" during a deliberate Skia-GPU Epiphany benchmark; two user photos of the panel vs a pixel-perfect grim capture taken minutes later
refutes: the rd_ptr pageflip patch tears cmd-mode transfers under load; corruption returning means the compositor dmabuf fix regressed; Skia-GPU is safe now that phoc advertises explicit modifiers; a clean screenshot proves the panel is clean
first-learned: 2026-09-01
---

**The question** — during heavy-page browser benchmarking the user photographed
garbled glyph blocks in the top-right panel area, then a frame stitched from
two different scroll positions, and then phosh degraded (no background,
transparent navbar). The rd_ptr pageflip patch (0204) had landed the same day
and touches exactly the flip-timing machinery. Did it introduce tearing?

**The answer** — one a5xx GPU fault + hangcheck recovery (t=2605 s, during
the deliberately-launched Skia-GPU benchmark variant) explains every
artifact. phoc survived the reset (the r51 fork's 0001/0002 exist for this)
and a grim capture minutes later is pixel-perfect; but phosh's own GL
textures died, leaving the empty background and transparent navbar, and the
command-mode panel kept whatever the last pre-reset transfers left in its
RAM -- which is why the debris and the mixed-scroll-position frame PERSISTED
instead of healing on the next repaint of an untouched region. A greetd
restart heals it fully.

**The discriminator worth keeping**: grim re-renders the compositor scene;
the panel shows what the DPU last transferred. grim clean + camera dirty
localises damage to panel RAM / post-reset debris, NOT to client buffers or
the compositor. Neither vsync counters nor screenshots can see this class of
artifact; the camera (or the user's eyes) is the only instrument, exactly as
the display handoff warned for frame rates.

**Skia-GPU is re-condemned on the FIXED stack.** The old condemnation
predated the phoc modifier fix, so it was retested deliberately: within
minutes of heavy-feed rasterization it faulted the GPU (status D301B1C1).
The corruption story was never about wayland modifiers. CPU rendering stays;
`WEBKIT_SKIA_CPU_PAINTING_THREADS=2` measured better than 4 on fling
(0-1 jank / max 22-47 ms vs 4 janks / max 100 ms; drag equal) and the
launcher now ships 2.

**What this rules out** — the rd_ptr patch tearing transfers (no artifact
occurred outside the reset's blast radius; its pending_kickoff_cnt gate
stands); the compositor dmabuf fix regressing (grim clean, session fling
still p50 16.7 / 0-jank); "a clean screenshot means the user is wrong".
