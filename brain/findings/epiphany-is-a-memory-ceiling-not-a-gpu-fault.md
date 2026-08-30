---
id: epiphany-is-a-memory-ceiling-not-a-gpu-fault
title: Epiphany on YouTube is a memory ceiling, and the GPU buffers are charged to its cgroup
scope: device:google-taimen
subsystem: graphics
severity: finding
confidence: proven
evidence: 2026-08-29, kernel 7.2.2. With Epiphany on YouTube - /proc/pressure/memory full avg10=7.04 while CPU sat 58-81% idle; /sys/kernel/debug/dri/0/gem Resident 2476 objects / 1,541,099,520 B; the app scope's memory.current 1925 MiB with shmem 1576 MiB and memory.high unset; pgscan_direct 152809, pswpout 212461, allocstall 1126, oom_kill 0. Closing Epiphany took GEM to 57 objects / 187,785,216 B on the same boot.
refutes: Epiphany is slow because of a GPU fault or hang; the NFC interrupt storm explains the jank; it is a compositor bug; hardware video decode alone would fix it; free(1) "shared" on this device is tmpfs
first-learned: 2026-08-29
---

**The question** — Epiphany on YouTube is unusable: very slow scrolling, visual
artifacts across the whole phosh session, and eventually a hang. Is that the
GPU?

**The answer** — no. It is a memory ceiling, and the decisive number is PSI:

    cpu     some avg10=2.77   full avg10=0.00     <- CPU 58-81% idle throughout
    memory  some avg10=7.16   full avg10=7.04     <- everything stalled, 7% of wall time
    io      some avg10=0.61   full avg10=0.31

`memory full` means *every runnable task* was blocked in reclaim. `some` and
`full` being nearly equal is reclaim thrash: when anything stalls, everything
stalls -- which is why the artifacts hit the whole session and not just
Epiphany's window.

**The memory is GPU buffers, and cgroup v2 charges them to the app.** msm GEM
objects are shmem-backed, so they appear as `shmem` in the cgroup and as
`shared` in `free`:

    app-gnome-org.gnome.Epiphany-*.scope
      memory.current 1925 MiB   peak 2055 MiB   memory.high  max
      shmem          1576 MiB
    /sys/kernel/debug/dri/0/gem   Resident 2476 objects, 1,541,099,520 B

1.9 GiB of a 3.6 GiB phone, in one app scope, **unbounded**. Per-process DRM
fdinfo puts nearly all of it in a single `WebKitWebProcess` (the YouTube tab).
Closing Epiphany dropped GEM to 57 objects / 187 MB on the same boot, which is
the control: ~1.35 GB was its working set.

It is a working set, not a leak -- sampled over 60 s it sat flat at 1.50-1.55 GB
and drifted *down*.

**Why nothing appears in dmesg** — nothing went wrong in the kernel. Zero GPU
faults, zero hangchecks, zero DRM `*ERROR*`. The kernel ran out of room and did
exactly what it should. An empty log is the expected observation here, not a
failed search.

**What this rules out**

- *"The NFC interrupt storm was the cause."* It was real and it was fixed
  (26M interrupts to zero, see
  [[a-level-irq-with-a-pull-up-storms-when-its-chip-is-off]]) and the symptom
  did not move. Two live defects at once; the loud one was not the reported one.
- *"It is a GPU fault or the a5xx hang."* No fault, no hangcheck, in dmesg or in
  a netconsole capture spanning the failure.
- *"`shared` in free(1) is tmpfs."* `/tmp`, `/run` and `/dev/shm` together held
  8 MB. All 1.58 GB of `Shmem` was GEM.
- *"Hardware decode alone would fix it."* It removes the software VP9/AV1 cost
  and some buffer churn, which is worth having, but the tile/layer working set
  of a QHD+ browser is the bulk of this and venus does not touch it.

**Untested mitigation** — `MemoryHigh=1500M` on the app scope, via
`~/.config/systemd/user/app-gnome-org.gnome.Epiphany-.scope.d/50-memory.conf`,
so reclaim is throttled inside the browser's cgroup instead of freezing the
session, and so WebKit's `MemoryPressureHandler` finally has a bound to react
against (with `memory.high` unset it never sheds anything). This is applied but
**not verified**: reproducing the load needs a real session, and Epiphany
launched from ssh dies with SIGABRT in GTK4/libepoxy. Measure it with
`memory full avg10` before and after on the same page.

---

## ADDENDUM 2026-08-30 — the mitigation is VERIFIED, and one claim above is CORRECTED

**CORRECTION.** This note said "It is a GPU fault or the a5xx hang" was ruled
out, on "no fault, no hangcheck, in dmesg or in a netconsole capture spanning
the failure." That reading was too strong. Under a heavier session the memory
ceiling **does** reset the GPU — three times in one boot on 2026-08-30:

    01:44:48  a5xx_irq gpu fault, status EE0011C1, ib1/ib2 BUFSZ=0000
              recover_worker: hangcheck recover!
              offending task: WebKitWebProces
    01:53:30  two more, offending task: SkiaGPUWorker
    01:44:48.549  phoc: [gles2/pass.c:315] GPU reset (innocent)
                  phoc: Re-creating renderer after GPU reset

The correct statement is narrower and more useful: **the memory ceiling is the
cause and the GPU fault is a downstream effect of it.** IB1/IB2 BUFSZ=0 with no
SMMU fault line means the GPU was busy and not retiring — it choked on the
working set, it did not chase a bad pointer. The original session simply never
drove it hard enough to see one.

This matters because the fault is what makes the corruption **session-wide**. An
a5xx recovery resets the GPU for every client, so phoc — ruled `innocent` by
GL_KHR_robustness, the guilty context being WebKit's — rebuilds its renderer and
re-uploads every texture. That is why the artifacts land in phosh's panel and in
other apps, not only in the browser window.

**The mitigation is now measured, and it works.** `MemoryHigh=1500M` on the
scope, applied at launch:

    | metric        | unbounded | MemoryHigh=1500M |
    |---------------|-----------|------------------|
    | scope         | 2097 M    | ~1400 M          |
    | scope swap    |  527 M    | **0 M**          |
    | GEM resident  | 1764 M    | ~670 M           |
    | MemAvailable  |  353 M    | ~1300 M          |
    | GPU faults    | 3 / boot  | **0**            |

It now ships in `device-google-taimen` r35 as
`usr/lib/systemd/user/app-gnome-org.gnome.Epiphany-.scope.d/50-memory.conf`.
The `~/.config` copy this note originally described was **erased by the fresh
install** hours after it was written, which is why the symptom came back —
package it, or it does not survive.

**Confirmed mechanism, not assumed:** `libwebkitgtk-6.0.so.4` contains
`/sys/fs/cgroup/%s/%s/%s`, `memory.current`, `memory.high` and `memory.max`, and
one second after the limit was applied three WebKitWebProcesses logged
`Memory pressure relief: ...` for the first time in 23 minutes of runtime.
WebKit sheds only when the cgroup gives it a ceiling to read.

**NEW HAZARD — do not apply this limit to a running browser.**
See [[memory-high-arms-systemd-oomd-against-the-browser]]. Setting it on a scope
already above the limit forces a reclaim storm that systemd-oomd kills the
process tree for. Set it at launch.

**Still open after this fix:** transient corruption of *small icons and glyphs*
inside WebKit only (phosh's own panel stays clean), visible in a `grim` capture
and therefore rendered that way rather than damaged on the way to the panel. It
survives with zero GPU faults, so it is NOT this bug. Suspect is WebKit 2.48's
multithreaded Skia GPU painting on a5xx — note `SkiaGPUWorker` was the task
blamed for two of the three faults above. Test knob:
`WEBKIT_SKIA_GPU_PAINTING_THREADS=0`.
