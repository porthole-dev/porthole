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
