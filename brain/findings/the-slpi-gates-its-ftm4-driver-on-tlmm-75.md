---
id: the-slpi-gates-its-ftm4-driver-on-tlmm-75
title: The SLPI refuses to arm a wake gesture unless tlmm 75 is already high, and it gives up after 500 ms
scope: device:google-taimen
subsystem: input
severity: finding
confidence: proven
evidence: "Decompiled from taimen/blobs/work/squashed/slpi_v2.mbn (ELF32, QUALCOMM DSP6, 3.9 MB) with Ghidra 12 headless, Hexagon:LE:32:default, via pyghidra; functions located by the name strings they log about themselves. ftm4_i2c_switched_to_slpi() at b2194b90 is a GPIO read of 0x4b (75) returning true only when the value is non-zero. ftm4_enable_sensor() at b2194c04 opens with `iVar10 = -0x32` and a do/while that calls it, sleeps 10000 us and increments until zero -- 50 iterations, 500 ms -- then logs 'switch not ready, time out', 'i2c switch not toggled, status = %d' and 'touch i2c port offline, aborting'. Sensor ids 0x10/0x11/0x12 map to gesture mask bits 1/2/4. DT: st,i2c-switch-gpios = <&tlmm 75 GPIO_ACTIVE_LOW>, so ftm4.c's gpiod_set_value(switch_gpio, 0) drives the line HIGH -- the polarity is already right. Also in the firmware: the sensor Android enables is com.google.sensor.double_tap_prox_gated, which requests double tap AND proximity and disables double tap while prox reads covered. Device-side null, taken 2026-09-19 with the operator present: IIO event enabled, input6/power/wakeup enabled, ftm4.keep_powered=1, a real 100 s s2idle -- slept the full alarm, woke on wakeirq=116 (RTC), wakeup_count 0."
refutes: "keep_powered plus the suspend-time bus handover is the missing ingredient for double-tap; the i2c switch polarity is inverted; the gesture sensors are silent because the touch controller is powered down; nothing in the SLPI drives the touch controller"
first-learned: 2026-09-19
---

**The SLPI has its own ftm4 driver.** `sns_dd_ftm4_init`, `ftm4_enable_sensor`,
`ftm4_check_chip_id`, `ftm4_configure_double_tap_parameters`,
`ftm4_best_effort_update_gesture_mask`, `ftm4_register_interrupt`. It talks i2c
to the touch controller itself; the AP's job is only to get out of the way.

**And it decides whether it may, by reading the mux pin itself:**

```c
bool ftm4_i2c_switched_to_slpi(void) {          /* b2194b90 */
    r = gpio_read(0x4b, &v);                    /* 0x4b == tlmm 75 */
    if (r) log("%s: failed to read gpio, status = %d");
    return r == 0 && v != 0;                    /* SLPI owns it when 75 is high */
}

/* ftm4_enable_sensor, b2194c04 */
i = -0x32;                                      /* -50 */
do {
    if (ftm4_i2c_switched_to_slpi() & 1) { ...chip id, reinit, gesture mask... }
    usleep(10000);
} while (++i != 0);                             /* 500 ms, then: */
log("%s: switch not ready, time out");
log("%s: touch i2c port offline, aborting");
```

**So the arming order matters, and ours is wrong.** Userspace writes
`in_index0_change_en` on an awake phone. The AP holds the mux, tlmm 75 is low,
the SLPI waits half a second and gives up. The suspend that hands the bus over
comes later, and nothing re-arms the sensor after it.

That kills the standing hypothesis -- that `ftm4.keep_powered` plus
`ftm4_suspend()`'s handover was the missing ingredient. It was tested properly
on 2026-09-19, with an operator, and produced a clean null.

**What is NOT established.** Whether handing the bus over *first* and arming
*then* makes the gesture fire. `ftm4.handover` (patch 0246) exists to try it
and has not been run with anyone at the phone. Two things to know before
trying:

- **The AP wake path is probably fine, so do not go fixing it.**
  `qcom_glink_smem.c` requests the SLPI's IRQ with `IRQF_NO_SUSPEND`, so it
  survives `suspend_device_irqs()`, and `qcom_smgr` already calls
  `pm_wakeup_event()` on the input device when a gesture arrives. Every
  `glink-smem` IRQ reads `wakeup=disabled` in sysfs and that is not evidence of
  anything -- `WLAN_CE_2` reads the same while awake and demonstrably wakes the
  phone, because ath10k arms it inside its suspend callback.
- **`ftm4_enable_sensor` has a second exit worth watching**, after the mux
  check: `"framework interrupt service not enabled"` and
  `"failed to register ftm4 signal on GPIO [%d]"`. The AP holds the ftm4
  interrupt (irq 131, msmgpio 125) and never releases it.
