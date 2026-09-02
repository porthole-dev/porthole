---
id: a-browser-arm-runs-on-a-throttled-phone-that-is-discharging-on-the-pc-port
title: Every browser arm runs at 75-78 C with the big cores capped to 1.0-1.5 GHz, and a phone on a PC's USB port discharges under that load until it browns out
scope: device:google-taimen
subsystem: power
severity: trap
confidence: proven
evidence: 2026-09-02 taimen: cpufreq cooling state 18/29, scaling_max 1267200 during ab.sh; pmi8998-charger usb_type [SDP], input 470 mA, battery current -290..-360 mA; 28% at 16:50, 6% and a reboot with no panic at 17:47
first-learned: 2026-09-02
---

**The trap** -- a browser measurement on this phone is a measurement of a
thermally capped, discharging device, and nothing in the numbers says so.

Within ~40 s of a YouTube page loading the hottest zone is at 75-78 C, the
DT's passive trip (`cpu*-thermal trip_point_0 = 75000`) engages, and
`cpufreq-cpu4` sits at cooling state ~18/29: `scaling_max_freq` 1.0-1.5 GHz
of 2.36 on the big cluster, 300-600 MHz of 1.9 on the little one. It stays
there for as long as the page plays. Compare arms only against each other at
the same temperature; print the hottest zone and the cap next to every
number (`tk-webarm.sh` does not yet; `thermal-arm` style loops do).

Meanwhile the charger reports `Charging` but the PC port is an SDP:
`pmi8998-charger usb_type = Unknown [SDP] DCP CDP`, input 470 mA at 4.98 V.
The load (panel + throttled CPU + GPU at 710 MHz + wifi) draws more, so the
battery nets -290 to -360 mA (`bms current_now`): 28% at 16:50 became 6% and
3.52 V at 17:47, followed by a reboot with nothing in pstore or the journal
-- a brown-out under a load spike, not a crash.

**The port was never the problem.** The PMIC reports `TYPE_C_STATUS_1 =
0x20` (UFP_TYPEC_RD3P0: the host advertises 3 A on CC) next to
`APSD_RESULT = SDP`; mainline `qcom_smbx` sets the input limit from BC1.2
alone and never reads the Rp bits it defines. Writing the advertised
current by hand -- `echo 3000000 > /sys/class/power_supply/pmi8998-charger/current_max`
-- took USBIN from 470 mA to 1.35 A and the battery from -300 to +915 mA
on the same PC port, no reboot. Kernel patch 0208 (`power: supply:
qcom_smbx: honour the Type-C current advertisement`) makes it permanent
from r28 on; before that kernel, the sysfs write is the fix and it does
not survive a reboot.

**Do** -- on a kernel before r28, write `current_max` after every boot; reach the phone over wifi if the port is needed for a charger
(`HOST=<wlan ip>`; every tool takes it from the environment), keep the
session's arms short, and read `cat /sys/class/power_supply/bms/capacity`
before a long unattended loop. **Do not** infer "fps does not track the CPU
clock, so the CPU is not the limit" from arms taken at different
temperatures: the cap moves between arms. It was checked properly once
(fps 27-30 at 1.13 and at 1.88 GHz, same page, same minute); see
[[epiphanys-frame-is-20ms-of-compositor-cpu-plus-a-10ms-gpu-tail-not-a5xx-batches]].
