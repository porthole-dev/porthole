---
id: forcing-3a-input-current-collapses-vbus-and-resets-the-usb-port
title: The Type-C Rp advertisement is ignored, so a 3 A source charges at 500 mA -- and AICL, not the driver, is what bounds the request
scope: soc:msm8998
subsystem: power
severity: finding
confidence: proven
evidence: "taimen 2026-09-02, pmi8998-charger, phone on a PC port reporting [SDP]. Ramp of current_max with 6 s settle: 500000 -> in 474 mA, VBUS 4.96 V, battery +15 mA; 900000 -> 856 mA, 4.86 V, +452 mA; 1500000 -> 1354 mA, 4.73 V, +915 mA; 2000000 -> 1285 mA, 4.73 V, +915 mA (saturated); 3000000 -> the write itself wedged and hardware AICL settled the limit back to 1700000. Host log showed `usb 3-1: reset high-speed USB device ... using xhci_hcd` with cdc_ncm unregister/rename cycles."
refutes: "writing current_max drops the USB gadget in software; the charger driver reruns APSD or takes DPDM on a current_max write; requesting 3 A is harmful on this port"
first-learned: 2026-09-02
---

**The question** — the default 500 mA input limit leaves the phone losing
charge under load, so `current_max` gets forced. At 3 A the USB gadget dies and
ssh over `usb0` goes with it. Does writing `current_max` drop the gadget?

**The answer** — no, and the driver could not do it if it wanted to.
`smb2_set_current_limit()` in `drivers/power/supply/qcom_pmi8998_charger.c`
writes exactly one register (`USBIN_CURRENT_LIMIT_CFG`, value/25000). There is
no APSD rerun and no DPDM handoff on that path -- `CMD_APSD/APSD_RERUN_BIT` is
only touched by `smb2_status_change_work()` when charger-type detection fails.

**It is electrical.** A host port advertising `[SDP]` sources 500 mA. Asking
for 3 A droops VBUS until the host resets the port; the gadget goes with the
port, not with the driver. The measured ceiling on this desk's port was
**~1.35 A at 4.73 V** -- above that, nothing more arrives and the charger's own
AICL walks the limit back down (3000000 written, 1700000 read back).

**The value that works is `1500000`:**

| `current_max` | USB in | VBUS | into battery |
|---|---|---|---|
| 500000 (default) | 474 mA | 4.96 V | **+15 mA** |
| 900000 | 856 mA | 4.86 V | +452 mA |
| **1500000** | **1354 mA** | 4.73 V | **+915 mA** |
| 2000000 | 1285 mA | 4.73 V | +915 mA (no gain) |
| 3000000 | -- | -- | write wedged, AICL clamped to 1.7 A |

At the default the battery gains +15 mA **while idle**; under browser load that
goes negative, which is
[[the-debug-cable-starves-the-battery]] and the reason
[[a-browser-arm-runs-on-a-throttled-phone-that-is-discharging-on-the-pc-port]]
exists. At 1500000 the same phone went 30% -> 71% during an hour of continuous
video arms.

**3 A is safe to request, and is what the source advertises.** Measured
again at 76% battery, writing 3000000: the hardware AICL settled the limit at
**2.05 A**, USBIN drew **1.9 A** at 4.53-4.61 V, the battery took +530-870 mA
under load, and there was **no USB disconnect and nothing in dmesg**. That is
better than the 1.5 A hand-set value (1.43 A in).

An earlier ramp on this same desk saturated near 1.35 A and one 3000000 write
appeared to wedge; that was a single event, was not reproduced, and should not
be read as causation -- the register write itself cannot drop the gadget (see
above). Where a source cannot supply what it advertises, AICL walking the
limit down is the designed behaviour, not a fault.

**It does not survive a replug.** `current_max` is reset by the charger's
detection path on every attach, so it has to be re-applied per session.

**How it was established** — a stepped ramp with a 6 s settle, reading
`current_now`/`voltage_now` on the charger and `current_now` on `bms`, with ssh
over **wlan0** so that losing `usb0` could not take the measurement with it.
That last part is not optional: reach the device over wifi before touching the
charger, or the experiment kills its own transport.
