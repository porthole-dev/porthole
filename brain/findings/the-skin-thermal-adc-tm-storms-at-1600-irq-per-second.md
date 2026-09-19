---
id: the-skin-thermal-adc-tm-storms-at-1600-irq-per-second
title: The skin thermal zone storms the PMIC ADC at ~1600 interrupts a second, awake and idle, in the shipped config
scope: device:google-taimen
subsystem: thermal
severity: finding
confidence: proven
evidence: "taimen, kernel 7.2.2 #65, idle, screen on, shipped config. /proc/interrupts: pm-adc-tm5 (irq 44) 2 254 577 since boot, measured at 1572-1589/s over repeated 5 and 10 s windows; pm-adc5 (irq 48) 979 939 since boot, 655-671/s. Disabling the skin-thermal zone (`echo disabled > mode`) takes pm-adc5 to 0/s and pm-adc-tm5 UP to 2308/s. Registers read through /sys/kernel/debug/regmap/0-00 (pmic-spmi, pm8998): STATUS_LOW 0x340a = 0x10, i.e. channel 4 latched, and it reads 0x10 again 2 s later, after set_trips rewrites, and after a driver unbind/rebind. Channel 4 block at 0x3480: 51 47 28 ff 7f 01 12 81 -- adc_channel 0x51 (ADC5_AMUX_THM5_100K_PU), low-voltage threshold 0x2847, high-voltage threshold 0x7fff, buf[5]=1 = ADC5_TIMER_SEL_2 (correct, the enum starts at 0), buf[7] = MEAS_EN | LOW_THR_INT_EN. Moving trip_point_0_temp 38000 -> 44000 -> 38000 rewrites 0x3481/82 as 47 28 -> ee 25 -> 47 28, confirming set_trips and configure() both run, and confirming the inverted mapping (hotter temperature = lower ADC code); STATUS_LOW stays 0x10 throughout, including against the 44 C code. Unbinding the driver takes the rate to 0/s and leaves STATUS_LOW at 0x10; rebinding restores 1558/s. DT: thermal-sensors = <&pm8998_adc_tm 4>, io-channels = <&pm8998_adc ADC5_AMUX_THM5_100K_PU>."
refutes: "the adc-tm interrupts come from crossing a thermal trip; the storm is caused by the skin-thermal zone's polling; rewriting the thresholds re-arms the monitor; re-probing the driver clears a stuck adc-tm latch; buf[5] is programmed with the wrong timer"
first-learned: 2026-09-19
---

**~1600 interrupts a second, forever, on an idle phone.** `pm-adc-tm5` has
fired 2.25 million times since boot and is still going at 1572/s, dragging
~670 VADC conversions a second behind it. This is the shipped configuration,
screen on, nothing running.

**It is not a trip crossing.** `skin-thermal` reads 32.1 C and its lowest trip
is 38 C. The PMIC's own comparator is latched: `STATUS_LOW` (0x340a) reads
`0x10` -- channel 4 -- and stays `0x10` across seconds, across threshold
rewrites, and across a driver unbind/rebind. The latch lives in the PMIC and
nothing mainline does can clear it.

**Mainline's ISR has no path that could.** `adc_tm5_isr()` reads
`ADC_TM5_STATUS_LOW`/`_HIGH`, and for any channel whose bit is set and whose
interrupt is enabled it calls `thermal_zone_device_update()` -- and writes
nothing back. It relies entirely on the thermal core coming back through
`set_trips`. The gen2 ISR right below it does clear, via the dedicated
`*_CLR` registers. The vendor's `qpnp_adc_tm.c` ISR opens with
`qpnp_adc_tm_disable(chip)` and then clears `LOW_THR_INT_EN` for the sensor
that fired, re-arming afterwards. Mainline does neither, so once the
comparator latches, the interrupt free-runs.

**And the comparator will not unlatch, because its measurement is wrong.** The
latch holds even when the threshold is moved to the 44 C code (0x25ee), which
is *further* from 32 C in the direction that should clear it. Whatever the
ADC-TM samples on channel 4 sits below any threshold that can be programmed,
while the IIO/VADC path on the same input reports a correct 32.1 C. The
temperature the thermal core sees is right; the hardware monitor behind it is
not converting usefully.

**The vendor does not use the monitor for this at all.** Our own DT comment
records it: *"vendor thermal-engine samples bd_therm2 every 2000 ms"*. The
skin sensor is **polled** downstream, not wired to a hardware threshold
monitor. We implemented it as an adc-tm zone, which is both a behaviour
mismatch and the source of this storm.

**Three ways out, cheapest first.**

1. **Poll it, as the vendor does.** Take `skin-thermal` off `pm8998_adc_tm`
   and give it a 2000 ms polling zone over the VADC IIO channel. A DTS change,
   the `boot` rung. Removes the storm outright and matches downstream.
2. **Make the ISR self-limiting**, vendor-shaped: clear `LOW_THR_INT_EN` for
   the channel that fired before calling into the thermal core, and re-arm in
   `set_trips`. Careful -- the core skips `set_trips` when the trip window is
   unchanged, so a naive disable loses the trip forever.
3. **Find out why channel 4's ADC-TM measurement reads below every threshold.**
   The most interesting answer and the most work. The config block matches what
   mainline intends, so the fault is upstream of it.

**Do not read this as a suspend problem.** During a 31 s s2idle the same
interrupt only manages ~47/s; the storm is an awake-and-idle cost, which is
exactly where this port's 378 mA blanked floor lives.
