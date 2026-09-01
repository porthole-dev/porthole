---
id: the-top-right-corruption-is-freedreno-gmem
title: The top-right corruption is freedreno's GMEM tile path, not a GPU fault -- the boundary is the a5xx bin column at x=1024
scope: soc:msm8998
subsystem: gpu
severity: finding
confidence: proven
evidence: grim screenshots + per-arm pixel statistics over the region x=1024..1439, y=0..93; kernel fault counter and phoc journal as controls
refutes: the top-right RGB noise comes from an a5xx GPU fault resetting the renderer; it is the WebKit memory ceiling; it is Skia-specific; it is a DPU or DSI scanout artefact; it is fixed by phoc's repaint-after-GPU-reset patch
first-learned: 2026-09-01
---

**The question** — a block of blocky colour noise appears at the top right of
the phosh panel whenever a heavy page (YouTube) is open in Epiphany. The
standing explanation, written into `taimen-epiphany-memory.conf` and the
2026-08-30 handoff, is that an a5xx GPU fault resets the GPU for every client,
phoc rebuilds its renderer, and static layer-surfaces keep whatever the dead
renderer left. Is that what is happening?

**The answer** — no. It happens with **zero GPU faults and zero renderer
re-creations**, and it is a bug in freedreno's GMEM (tiled) rendering path.

Reproduced on kernel 7.2.2 #23, mesa 26.1.6, phoc 0.57.0-r50, webkit2gtk
2.48.1-r50, with the fault counter read before and after every arm:

    sudo dmesg | grep -c 'gpu fault'                     -> 0
    journalctl --user | grep -ci 'GPU reset|Re-creating' -> 0

while the corruption was on screen and in the compositor's own output
(captured with `grim`, so it is in the composited buffer, not the panel).

**The boundary names the mechanism.** Measured by differencing a clean frame
against a corrupt one from the same session:

    corrupt columns: 1024 .. 1439   (416 wide)
    corrupt rows:       0 ..   93   ( 94 tall, the phosh panel damage rect)

x = 1024 exactly. The a540 has 1 MiB of GMEM (`a5xx_catalog.c`, `.gmem =
SZ_1M`), and at 4 bytes per pixel that is 262144 pixels -- a 1024x256 bin.
A 1440-wide output is therefore two bin columns, 0..1023 and 1024..1439, and
**the corruption is exactly the second one**. The garbage is recognisable
image data from the page, which is what stale GMEM contains.

**The arms.** Same video page, same swipes, flag verified present in the
running compositor's environment each time (`/proc/<phoc>/environ`), region
classified by whether it is greyscale (clean panel) or colourful (corrupt):

    arm                                       result
    default (GMEM tiling + hw binning)        CORRUPT, 3/3 separate launches
    FD_MESA_DEBUG=nobin (GMEM, no hw binning) CORRUPT, 4/6 samples
    FD_MESA_DEBUG=sysmem (no GMEM tiling)     CLEAN,   5/5 samples

so it is the GMEM tile restore/resolve, **not** the hardware binning or the
visibility stream. A full repaint of the region (open the top drawer) clears
it; it returns within seconds of the page rendering again, because only a
partial repaint leaves parts of the bin uncovered by any draw.

**What this rules out**

- *An a5xx GPU fault is behind it.* There were none. The fault class is real
  and separate -- see the hang-detect threshold note -- but it is not this.
- *The WebKit memory ceiling.* MemoryHigh was applied and psi was zero.
- *Skia, or WebKit at all, specifically.* The corruption is in phoc's own
  composition of the phosh panel; WebKit only supplies the workload.
- *The DPU or the DSI link.* Only one plane is in use (plane-0, full screen,
  XR24, modifier 0) and `grim` -- which copies the compositor's output, not
  the panel -- sees the corruption.
- *phoc's repaint-after-GPU-reset patch fixed it.* That patch is installed
  (0.57.0-r50) and this still happens.

**How it was established** — `~/.phoshdebug` is sourced by `phosh-session`,
so an arm is: write the export there, `pkill -x phoc`, log back in, confirm
the variable is in `/proc/$(pgrep -x phoc)/environ` (this is the positive
control -- an arm without it proves nothing), open the page, sample with
`grim`, and classify the region. A frame where the page has not actually
rendered is not a null: check the screenshot shows loaded content before
believing a CLEAN.

**What would overturn it** — a corrupt sample under `sysmem` with the flag
confirmed in phoc's environ and the page confirmed rendered. Nothing else.

**Still open** — where in `fd5_gmem.c` the second bin column goes wrong
(restore vs resolve), and what `sysmem` costs in bandwidth, power and heat on
this panel. `FD_MESA_DEBUG=sysmem` for the compositor is a workaround with an
unmeasured price, not yet a fix.
