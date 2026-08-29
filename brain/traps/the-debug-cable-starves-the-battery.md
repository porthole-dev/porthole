---
id: the-debug-cable-starves-the-battery
title: A battery that will not charge is usually the debug cable, not the driver
scope: generic
subsystem: power
severity: trap
confidence: proven
evidence: "taimen 2026-08-29, same kernel, same driver, two cables. Host USB port: usb_type=[SDP], current_max=500000, usb draw 474mA, battery current +105mA idle and -479mA under load, capacity pinned at 71%. Wall charger: usb_type=[DCP], current_max=1500000, usb draw 1441mA, battery current +910mA, capacity climbing. No code changed between the two readings."
first-learned: 2026-08-29
---

**Before investigating a charging bug, look at what the phone is plugged into.**
During bring-up that is nearly always the development machine, because that is
the cable carrying ssh. A host port is a Standard Downstream Port and the spec
caps it at 500 mA. A phone with its display and radios up draws more than that,
so the deficit comes out of the battery **while it is plugged in**.

What it looks like, and why each symptom misleads:

- `status` says `Charging` while `current_now` is negative. Not a lie: the
  charger FSM really is in `FAST`/`TAPER_CHARGE`, pushing its 475 mA into the
  system rail. It says nothing about the battery's net direction.
- The desktop battery icon shows discharging anyway, because UPower looks at
  the rate as well as `status`. The UI is more correct than the kernel property
  it is reading, which inverts the usual instinct about where the bug is.
- Capacity sits still for hours, so it reads as a broken gauge.
- It comes and goes with load, so it reads as flaky hardware or a race.

The one-command discriminator:

```sh
cat /sys/class/power_supply/*/usb_type      # [SDP] = host port, 500 mA
cat /sys/class/power_supply/*/current_max   # 500000 vs 1500000
```

`[SDP]` with `current_max=500000` is the cable, not the driver. Plug into a
wall charger and re-read: if it becomes `[DCP]` at 1500000, everything works
and there is nothing to fix.

**The measurement problem this creates:** unplugging from the host to test a
wall charger also removes ssh over usb0, and the device goes ABSENT. Reach it
over wlan0 instead -- `ip neigh` on the host, or the addresses netconsole
recorded -- or you cannot observe the very state you are trying to test.

Do not "fix" the icon. A phone that is losing charge should say so; a charging
icon over a draining battery is the worse bug. If it must charge under load,
that is a charger question, not a kernel one.
