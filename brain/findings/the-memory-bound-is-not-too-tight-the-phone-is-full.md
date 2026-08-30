---
id: the-memory-bound-is-not-too-tight-the-phone-is-full
title: The Epiphany memory bound is not too tight -- the phone is genuinely full, and the swap is zram
scope: device:google-taimen
subsystem: memory
severity: finding
confidence: proven
evidence: taimen 2026-08-30, kernel 7.2.2, device-google-taimen r36, Epiphany on YouTube with the WebKit sandbox ON
refutes: MemoryHigh=1500M is tighter than it needs to be; raising it will cut the a5xx faults
first-learned: 2026-08-30
---

**The question** — Epiphany's scope sits at 1394 MiB against a `MemoryHigh=1500M`
bound with several hundred MB swapped out. That reads like a bound set too
tight. Is raising it the way to cut the a5xx GPU faults?

**The answer** — no. The bound is not the constraint; the phone is full. Raising
`MemoryHigh` does not create memory, it only moves where the pressure is paid,
and on this device the swap is **zram**, so "swapped out" does not mean "off the
phone".

Measured during YouTube playback, sandbox on, 13 minutes in:

    memory.high        1500 MiB        memory.current   1408 MiB
    memory.events high 5809            <- times the cgroup was throttled
    memory.stat shmem   826 MiB        <- GEM, charged to the app
    pgmajfault       223190            pgscan 1799100 / pgsteal 698446

    free -m:  total 3666  used 1905  free 265  shared 1018  available 424
    zram0:    orig 1140 MiB -> compr 547 MiB, mem_used 559 MiB, ratio 2.08x

**424 MiB available, with 1140 MiB already compressed into zram.** There is no
headroom to hand back. The earlier reading of "513 MiB free" was taken as spare
capacity; it is the margin reclaim is fighting to keep, and it shrinks as the
session runs: over eleven minutes swap climbed 555 -> 1241 MiB and
`psi_full avg60` climbed 0.98 -> 1.93 while faults accrued 5 -> 11.

Two things follow that the "raise it" theory misses:

- **zram costs RAM and CPU.** 1140 MiB of swapped pages still occupy 559 MiB of
  real memory, so the net win is roughly half of what /proc/swaps suggests. And
  every page in or out is a compress or decompress on a device already burning
  ~525% of a core on software video decode. Reclaim is competing with the
  workload that caused it.
- **The working set is the problem, not the ceiling.** Seven WebKitWebProcesses
  for one tab: one at 523 MiB and six at 165-172 MiB, ~1.5 GiB between them.
  A bound cannot make that fit; only shrinking it can.

**What this rules out** — that recalibrating `MemoryHigh` upward is a fix for the
a5xx faults. It is not. It trades 5809 throttle events for a thinner reclaim
margin and a real OOM risk, on a phone whose MemAvailable is already 424 MiB.

It does **not** rule out that memory pressure feeds the faults. That link is
still live and still plausible -- 826 MiB of shmem-backed GEM under active
reclaim is exactly the shape of a stall that trips a5xx hangcheck. What is dead
is the specific remedy, not the mechanism. Keep [[epiphany-is-a-memory-ceiling-not-a-gpu-fault]].

**How it was established** — `memory.events`, `memory.stat`, `/sys/block/zram0/mm_stat`
and per-process `VmRSS` read directly off the device while video played, with
frame-hash and CPU-jiffy controls confirming the path was live on every sample.

The lever that shrinks the working set is **hardware decode**: it removes the
software decoder's buffers and ~5 cores of CPU, which also stops zram
compression competing for the same cores. That needs the WebKit sandbox patch,
because with the sandbox on `/dev/video7` is invisible and decode is software no
matter what the GStreamer ranks say -- see [[the-av1-demotion-deleted-the-v4l2-ranks]].

**What would overturn this** — the same measurement with hardware decode actually
active. If MemAvailable then sits comfortably above ~800 MiB and the faults
persist, the bound rather than the working set becomes the thing to tune, and
this note stops applying.
