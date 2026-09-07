---
id: hardware-decode-works-in-webkit-the-ceiling-is-webkits-process-count
title: Hardware decode does work in Epiphany -- the residual ceiling is WebKit's ~7 processes per tab, not the decoder and not GEM runaway
scope: device:google-taimen
subsystem: media
severity: finding
confidence: proven
evidence: 2026-09-01, kernel 7.2.2 #25 aport r24. ph-mempressure.sh at 1-2 Hz across two live YouTube sessions driven by the user; two-arm gst-launch positive/negative control on a generated VP9 1080p30 clip; /dev/video7 fd holders; per-process VmRSS; dmesg fault census.
refutes: venus is idle during Epiphany playback; WebKit never sheds at memory.high; GEM grows monotonically past memory.high; the top-right/frame-drop symptoms are a5xx GPU faults; the v4l2 element ranks are wrong; the WebKit sandbox hides /dev/video7
first-learned: 2026-09-01
---

**SUPERSEDED IN PART, same day.** The *stutter* described here is not the
working set: it is the lost 60 Hz commit pipelining. See
[[the-60fps-commit-pipelining-was-lost-in-the-7-2-rebase]]. What stands
below is the memory and hardware-decode accounting.

**The question** — with the WebKit sandbox patch in and venus enabled, is
Epiphany actually getting hardware video decode on YouTube, and is the memory
runaway that [[the-memory-bound-is-not-too-tight-the-phone-is-full]] described
still the ceiling? `HANDOFF-2026-09-01-VIDEO-WIFI.md` §4 predicted GEM would
grow monotonically past `memory.high` because WebKit does not count DRM memory
against its own budget.

**The answer** — hardware decode works, and it is sustained. Playback has two
phases, and measuring only the first is what produced the runaway reading:

| phase | `hwdec` | scope vs `memory.high` | what the user sees |
|---|---|---|---|
| startup, ~30 s | **no** | climbs to the 1500M wall, ~600M into zram | "playback start took so much" |
| steady state | **yes**, 25/25 samples | oscillates 1383..1500M | decodes, but drops frames |

In steady state `psi_full` *decays* (8.46 -> 1.02) and the scope repeatedly
touches 1499/1500M and falls back to ~1390-1440M. That bounce is WebKit
shedding at its bound. It is not failing to shed, and GEM does not run away --
it plateaus at ~1420-1580M.

The CPU cost is gone: 47% idle with the top WebKit process at 31% of one core,
against the ~525% of a core software decode used to burn.

**What remains** is the working set, not the decoder. **Seven WebKit web
processes at ~252 MB each for a single YouTube tab**, against a 3666 MB phone.
That holds `MemAvailable` at ~455-580M and `psi_full` around 11%, and a
compositor stalled in reclaim 11% of wall time misses vsync deadlines --
which is the dropped and re-presented ("rollback") frames.

**What this rules out**

- **venus is idle during Epiphany playback.** It is not. `hwdec=yes` for 25 of
  25 consecutive samples, `video-decoder` reads `auto/active`, and one
  WebKitWebProcess holds 4 fds on `/dev/video7`. **Zero** venus/vidc/hfi errors
  in dmesg for the whole boot.
- **The probe that said otherwise was being read too early, not lying.** Two
  arms on a generated VP9 1080p30 clip: `v4l2vp9dec` -> 31 yes/2 no,
  `vp9dec` -> 1 yes/27 no. It distinguishes hardware from software decisively.
  The `hwdec=no` runs were real, and they were the startup phase.
- **WebKit never sheds at `memory.high`** — refuted by the 1383..1500M
  oscillation above.
- **GEM grows monotonically past `memory.high`** — it plateaus. Also note
  `/sys/kernel/debug/dri/0/gem` is **system-wide DRM resident, every client**,
  not the browser's alone; reading it as WebKit's own budget is a category error.
- **The frame drops are a5xx GPU faults.** Two faults in a 35-minute boot, both
  in the same second (t=2055, `EE0011C1`, hangcheck recover x2). Two recoveries
  cannot produce continuous dropping.
- **The v4l2 element ranks are wrong.** `v4l2vp9dec`/`v4l2h264dec`/`v4l2h265dec`
  are all primary+1 (257), above `vp9dec` (256). Already correct.
- **The sandbox hides `/dev/video7`.** It is visible inside the web process,
  and `readlink /proc/PID/root` is `/` -- that process is not in a mount
  namespace at all.
- **AV1.** No dav1d or av1 decoder threads appear during playback; the element
  present is `v4l2vp9dec0`. `dav1ddec` is installed at primary (256) and would
  take AV1 if it were served, but it is not being used.

**How it was established** — `ph-mempressure.sh` at 1-2 Hz across two live
YouTube sessions the user drove by hand, with playback confirmed by the user
and by the positive controls the tool demands (GPU pinned 710 MHz, tmax 74-76 C).
The decoder probe was validated in both directions before any conclusion was
drawn from it. Process census by `VmRSS`; fd holders of `/dev/video7` by
scanning `/proc/*/fd`; fault census by `dmesg`.

**Two instrument traps paid for here.** `sudo -E` is refused by this busybox
sudo ("preserving the entire environment is not supported"), so
`PORTHOLE_INTERVAL=... sudo sh ph-mempressure.sh` silently samples at 1 Hz -- pass
`sudo env PORTHOLE_INTERVAL=...` instead. And a detached sampler started over ssh
died exactly when the scope hit its bound, losing the window it existed to
capture; write to a file on the device and expect to lose the tail.

**What would overturn this** — cutting WebKit's process count for one tab and
finding that `psi_full` and the frame drops persist with `MemAvailable`
comfortably above ~800M. That would move the ceiling somewhere else. The
knobs worth trying are on the WebKit side, not the kernel's:
`SiteIsolationEnabled` is a feature flag in this build (2.48.1), and
`WEBKIT_DISABLE_MEMORY_PRESSURE_MONITOR` exists, which means the pressure
monitor is on by default -- consistent with the shedding actually observed.
