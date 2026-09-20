---
id: a-tap-on-an-armed-suspended-screen-hangs-the-resume
title: The overnight resume hang is a touch on a wake-gesture-armed suspended screen, not a suspend regression
scope: device:google-taimen
subsystem: power
severity: finding
confidence: proven
evidence: taimen 7.2.2 #76, 2026-09-20. With 0-0049/power/wakeup=enabled, a tap on the suspended screen ended in bootreason=watchdog 4 times out of 4 (reset 159s, 162s and 187s after SUSPEND_ENTER, i.e. 12-68s after the tap). Without a tap, 7 cycles resumed cleanly: 300s and 900s RTC, 600s woken by the power key (wakeirq=38 pm8941_pwrkey), an armed-but-untouched 300s RTC cycle, plus pm_test freezer/devices/platform. Root cause: ftm4_irq() runs during s2idle where i2c is unreachable, fails the read instead of draining the level-triggered line, and IRQF_ONESHOT re-fires it forever. Fixed by kernel commit 'Input: ftm4 - do not read the controller from the wake gesture's interrupt' (aport patch 0254); after it, a double tap wakes the phone 3/3 with wakeirq=141 (msmgpio 125 ftm4) and the resume decodes 'double tap: 22 01 31 65 48 46 00 00'
refutes: suspend/resume is broken on 7.2; the overnight watchdog reset was a duration-dependent hang; arming the wake gesture is itself enough to hang the resume; double-tap was ruled out as the cause because kernel #71 carried it
first-learned: 2026-09-20
---

**The question** — a phone left overnight, cabled and at 99 %, idle-suspended
at 03:01:20 and never wrote another line. The owner pressed power eight hours
later and it came up with `androidboot.bootreason=watchdog`. What hung, and was
suspend/resume regressing?

**The answer** — nothing about the duration mattered, and suspend was not
regressing. The phone had the ftm4 wake gesture armed, and **a touch on an
armed, suspended screen hangs the resume**. Picking the phone up to press the
power key is a touch.

`ftm4_suspend()` arms the gesture and deliberately leaves the interrupt live so
the controller can raise it on a double tap. A tap therefore runs `ftm4_irq()`
while the system is still suspended, and i2c is unreachable there -- runtime PM
is disabled for the duration of a system sleep -- so `ftm4_read_event()` fails
instead of draining the event FIFO. The line is level triggered and the part
holds it asserted until that FIFO is empty, so `IRQF_ONESHOT` unmasks and the
handler is called straight back. The spin never lets the resume finish and the
30 s watchdog ends it.

**The measurements** —

| condition | result |
|---|---|
| armed + a tap | **4 resets out of 4**, `bootreason=watchdog`, 12-68 s after the tap |
| armed, untouched, RTC wake | resumed, 301 s |
| not armed, 300 s RTC | resumed |
| not armed, 900 s RTC | resumed, exactly 900 s |
| not armed, power-key wake | resumed, `wakeirq=38` `pm8941_pwrkey` |
| `pm_test` freezer / devices / platform | all clean, slowest callback `ftm4_resume` 86 ms |

**What this rules out** —

- "Suspend/resume is broken on 7.2." Seven cycles resumed cleanly the same day.
- "The hang is duration dependent." 900 s unarmed resumed; 12 s after a tap did
  not. Duration is not the variable, the tap is.
- "Arming the wake gesture is enough to hang it." An armed cycle woken by its
  RTC alarm resumed normally. The gesture must actually fire.
- "Double-tap is not the cause, because kernel #71 carried it and resumed 2/2."
  #71 resumed because nobody touched the glass during those two cycles.
- It also rules out the instruments people reach for first: see
  [[no-log-channel-survives-s2idle-on-this-device]].

**The fix** — do not read the controller from the handler while a gesture is
armed: mask the line, leave the event in the FIFO, and let `ftm4_resume()`
drain it once i2c is back. That is also the only place the event can be
decoded, so `KEY_WAKEUP` is reported for `FTM4_EV_GESTURE` carrying
`FTM4_GESTURE_DOUBLE_TAP` and for nothing else. Kernel commit "Input: ftm4 - do
not read the controller from the wake gesture's interrupt", aport patch 0254.
After it a double tap wakes the phone: 3 cycles out of 3, `wakeirq=141`
(`msmgpio 125 Level ftm4`), and the resume logs
`double tap: 22 01 31 65 48 46 00 00`. Ordinary touch still works afterwards --
the interrupt counter went 1 to 1897 across twenty seconds of touching, which
is the check that the `disable_irq()` depth stayed balanced.

**How it was established** — `tools/ph-suspend-cycle.sh` with a prep hook
setting `0-0049/power/wakeup=enabled`, an RTC alarm as the backstop, and an
operator tapping at an announced wall-clock time. The oracle is binary and
needs no log: either the boot_id changed or it did not. What would overturn it
is a reset in an armed, genuinely untouched cycle -- so record whether anything
touched the glass, because that is the whole variable.
