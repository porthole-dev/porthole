---
id: the-slpi-subscription-must-come-after-the-handover
title: The SLPI wake-gesture subscription is spent on arrival: subscribe AFTER the handover, never at boot
scope: device:google-taimen
subsystem: input
severity: trap
confidence: proven
evidence: taimen 7.2.2, 2026-09-20. A udev rule enabling qcom-smgr-double-tap's in_index0_change_en at boot produced nothing: blanked screen, ftm4.handover=1 applied by a trigger on the panel's dpms edge, operator double-tapping several times, no wake. Re-ordering the same trigger to write handover=1 first, sleep 1, then toggle in_index0_change_en 0 then 1, woke the phone on the next double tap, same boot and same kernel. Matches the firmware: ftm4_enable_sensor() in slpi_v2.mbn polls ftm4_i2c_switched_to_slpi() (tlmm 75) 50 x 10 ms and then logs 'switch not ready, time out' / 'touch i2c port offline, aborting', with nothing that retries later
first-learned: 2026-09-20
---

**Symptom** — every piece is in place and the gesture is silent. The bus is
handed over, `ftm4.handover` reads `Y`, the AP's interrupt is gone from
`/proc/interrupts`, `in_index0_change_en` reads `1`, and a double tap on the
blanked screen does nothing. Several taps, no wake.

**Cause** — the subscription is acted on **once, when it arrives**, and it is
thrown away if the AP still owns the mux at that instant.
`ftm4_enable_sensor()` in `slpi_v2.mbn`:

```c
i = -0x32;                                  /* -50 */
do {
    if (ftm4_i2c_switched_to_slpi() & 1) { ...chip id, reinit, gesture mask... }
    usleep(10000);
} while (++i != 0);                         /* 500 ms, then gives up: */
log("%s: switch not ready, time out");
log("%s: touch i2c port offline, aborting");
```

Nothing retries after that. So a subscription enabled at boot -- the obvious
place, and what a udev rule does -- is spent half a second later, hours before
the screen ever blanks. `in_index0_change_en` still reads `1` afterwards, which
is why this looks like it should work: **the attribute reflects the AP's
intent, not whether the SLPI is actually watching.**

**Fix** — order it, every time the screen blanks:

1. hand over the mux and the interrupt (the kernel's `drm_panel` follower does
   this; writing `ftm4.handover=1` is idempotent with it)
2. only then write the gesture enable, toggling it `0` then `1` if it was
   already set, so a fresh subscription is actually issued

**The measurement** — same boot, same kernel, one difference:

| subscription | result |
|---|---|
| enabled at boot, handover later | nothing, several double taps |
| re-issued after the handover | woke on the next double tap |

**Why it is worth a note** — the failure is silent and every readback looks
healthy, so it invites the conclusion that the SLPI path does not work at all.
It took an earlier session's null exactly that way. The companion finding is
[[the-slpi-needs-the-irq-not-just-the-mux]]: the mux and the interrupt are two
separate gates, and this is a third thing again -- ownership is necessary, and
the subscription still has to arrive after it.
