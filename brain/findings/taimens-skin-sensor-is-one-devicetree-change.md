---
id: taimens-skin-sensor-is-one-devicetree-change
title: taimen's skin thermistor needs no driver: bd_therm2 is VADC 0x51 and mainline already scales it
scope: device:taimen
subsystem: power
severity: finding
confidence: proven
evidence: Vendor: arch/arm64/boot/dts/lge/msm8998-taimen-pm.dtsi declares pm8998_vadc chan@51 ratiometric scale-function 2 and mirrors it into pm8998_adc_tm with qcom,thermal-node btm-channel-number 0x80; overlay-taimen-rev-1_0.dts (our board, androidboot.revision=rev_10) relabels it bd_therm2. Mainline: qcom-spmi-adc5.c:599 adc5_chans_rev2[ADC5_AMUX_THM5_100K_PU] = ADC5_CHAN_TEMP with SCALE_HW_CALIB_THERM_100K_PULLUP, and ADC5_AMUX_THM5_100K_PU == 0x51 in dt-bindings/iio/qcom,spmi-vadc.h:169. pm8998.dtsi:104-112 already has pm8998_adc_tm: adc-tm@3400 compatible qcom,spmi-adc-tm-hc with #thermal-sensor-cells, status=disabled; qcom-spmi-adc-tm5.c:1050 binds that compatible. Live device declares only ref_gnd/vref_1p25/die_temp on adc@3100.
refutes: mainline has no skin temperature support; the board thermistor needs a new driver or reverse engineering; we cannot reproduce the vendor thermal policy
first-learned: 2026-09-19
---

**The question** — the entire vendor thermal policy runs on skin temperature
(`bd_therm2`), and our port has no skin thermal zone. How much work is that —
a new driver? Reverse-engineering an unknown thermistor?

**The answer** — neither. It is a devicetree change. Every piece already exists
upstream and the channel numbers match exactly.

Vendor side, `arch/arm64/boot/dts/lge/msm8998-taimen-pm.dtsi` plus
`overlay-taimen-rev-1_0.dts` (**our** board — `/proc/cmdline` says
`androidboot.revision=rev_10`):

```dts
&pm8998_vadc   { chan@51 { label = "bd_therm2"; qcom,scale-function = <2>;
                           qcom,calibration-type = "ratiometric"; }; };
&pm8998_adc_tm { chan@51 { label = "bd_therm2"; qcom,thermal-node;
                           qcom,btm-channel-number = <0x80>; }; };
```

`scale-function = <2>` is a 100 kOhm pull-up NTC. Mainline, today:

```
vendor pm8998_vadc         mainline adc5_chans_rev2[]   (qcom-spmi-adc5.c:599+)
 0x4c xo_therm         ==  ADC5_XO_THERM_100K_PU   0x4c   THERM_100K_PULLUP
 0x4d pcb_rev          ==  ADC5_AMUX_THM1_100K_PU  0x4d
 0x4f pa_therm1        ==  ADC5_AMUX_THM3_100K_PU  0x4f   THERM_100K_PULLUP
 0x50 pa_therm2        ==  ADC5_AMUX_THM4_100K_PU  0x50   THERM_100K_PULLUP
 0x51 bd_therm2 (SKIN) ==  ADC5_AMUX_THM5_100K_PU  0x51   THERM_100K_PULLUP
```

(constants: `dt-bindings/iio/qcom,spmi-vadc.h:164-169`). And the
threshold-monitor half is already in our own DT, `pm8998.dtsi:104-112`:

```dts
pm8998_adc_tm: adc-tm@3400 {
        compatible = "qcom,spmi-adc-tm-hc";   /* qcom-spmi-adc-tm5.c:1050 binds it */
        #thermal-sensor-cells = <1>;
        status = "disabled";                  /* the only thing in the way */
};
```

So: enable `&pm8998_adc_tm`, add channel `0x51` to it and to `&pm8998_adc`, hang
a thermal zone off it. No driver, no new scaling table, no RE.

**What this rules out** —
- *"Mainline has no skin temperature support."* It has the channel, the 100k
  pull-up scaling and the ADC_TM driver, all upstream.
- *"We cannot reproduce the vendor thermal policy."* The policy numbers are
  known (see [[the-vendor-runs-four-thermal-layers]]) and now so is the sensor.
- *"The board thermistor is an unknown part."* It is on a documented VADC
  channel with the generic 100k pull-up table, which is what the vendor uses too.

**SHIPPED AND VALIDATED ON HARDWARE, 2026-09-19** (aport pkgrel 57, patch
0238). The channel reads, and it is unambiguously a case thermistor:

```
             idle      after 100 s of 8-thread burn
 skin        33.7 C ->  37.6 C     (+3.9, peaking 37.7 ~10 s AFTER the load)
 junction    39.6 C ->  74.8 C     (+35.2, saturates at ~73.8 after 20 s)
 pmic die    37.0 C ->  49.3 C
 battery     35.8 C ->  37.5 C
```

At rest the ordering is physical -- skin 33.7 < battery 35.8 < pmic die 37.0 <
junction 39.6 -- and under load skin rises **17x more slowly** than junction and
keeps climbing smoothly (~0.26 C per 5 s) after junction has saturated. That
damping is the signature of a sensor on the case, not on a die, and it is the
whole argument for the vendor's policy in one dataset: **junction saturates at
73.8 C and stops carrying information, while skin keeps integrating.** A 100 s
full-CPU burn ends at 37.6 C, just under the vendor's first cap at 38 C.

**The cooldown is the conclusive half.** When the load stops:

```
 t+0s   junction 74.8   skin 37.55   (load drops here)
 t+11s  junction 65.1   skin 37.71   <-- skin still RISING, and this is its peak
 t+36s  junction 42.5   skin 36.72
 t+61s  junction 41.2   skin 36.04
 t+86s  junction 40.6   skin 35.68
```

Junction sheds 32 C in 36 s while skin **overshoots upward for another ~10 s**
and then decays at roughly 1 C/min. A die sensor cannot do that: heat is still
flowing out of the die into the case after the die has cooled. This is thermal
mass, and it is proof the thermistor sits on the case.

**It also constrains how the ladder must be built.** Skin lags the die by
30-60 s, so a skin-driven policy needs slow sampling and generous hysteresis or
it will oscillate -- which is exactly what the vendor ships: `sampling 2000` ms,
`set_point_clr` one degree under `set_point`, and an `ss` controller with a
**time_constant** rather than a step-wise reaction. Do NOT drive this sensor
with `step_wise` and the 250 ms `polling-delay-passive` the junction zones use.


**How it was established** — vendor DT and the rev_10 overlay read directly;
mainline channel table, binding constants and driver compatibles read from the
tree; then landed and measured as above.

**One gap remains in the plumbing:** `CONFIG_QCOM_SPMI_ADC_TM5 is not set`, so
the threshold-monitor driver is not built and the `skin-thermal` zone does not
register -- the reading is available through IIO
(`in_temp_skin_therm_input` on the pm8998 VADC) but not yet as a thermal zone
with trips. Enabling that config is the prerequisite for wiring the ladder.

Overturned by: channel 0x51 reading a value that does not move with case
temperature -- which is now refuted.
