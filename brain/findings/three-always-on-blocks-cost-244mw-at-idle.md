---
id: three-always-on-blocks-cost-244mw-at-idle
title: 244 mW of taimen's screen-off idle is three blocks nothing is using
scope: device:taimen
subsystem: power
severity: finding
confidence: proven
evidence: taimen 2026-09-19, kernel 7.2.2 #57, aport pkgrel=56, screen off (bl_power=4), cabled. Interleaved A/B, 90 s blocks, median of pmi8998-charger/current_now: BASE 257851 / 253247 uA, PARK 207202 / 207202 uA (identical both rounds); baseline-to-baseline spread 4.6 mA. PARK = unbind imx362 + imx179, video-codec power/control=auto, unbind easel-mipi; all rebound and verified after. venus and both sensors showed runtime_suspended_time=0 over the whole 8 h 55 m uptime.
refutes: the idle drain is all the missing RPM handshake; the camera costs nothing when no camera app is running; venus-always-on is a theoretical cost
first-learned: 2026-09-19
---

**The question** — the phone drains with the screen off. Everyone reaches for
the RPM handshake (`vmin Count: 0`), which is real but is BP-13/BP-14-sized work.
Is there anything *above* that floor, and how big is it?

**The answer** — yes: **48.3 mA at ~5.06 V, about 244 mW**, from three hardware
blocks that nothing on the device is using. Interleaved A/B, two rounds, 90 s
blocks, screen off:

| arm | median USBIN |
|---|---|
| BASE round 1 | 257 851 uA |
| PARK round 1 | 207 202 uA |
| BASE round 2 | 253 247 uA |
| PARK round 2 | 207 202 uA |

Baseline-to-baseline spread is 4.6 mA, so the 48.3 mA delta is ten times the
noise floor, and the two PARK medians are bit-identical.

The three blocks:

1. **Both camera sensors are pinned runtime-active from probe to unbind.**
   `imx362` (rear) and `imx179` (front) read `control=auto`,
   `runtime_status=active`, `runtime_active_time` = the whole uptime,
   `runtime_suspended_time=0`. Cause is an unreleased `pm_runtime_get_noresume()`
   at the end of probe, and the code says so: `imx179.c:1116-1125` — *"Deliberately
   NO pm_runtime_idle() here yet … `clk_prepare_enable()` on MCLK1 returns -EBUSY
   once CAMSS_TOP has collapsed … it costs idle power and must not survive past
   bring-up."* This also pins `ca0c000.cci` and the `camss_top` GDSC: unbinding
   the two sensors moved CCI to `suspended` in the same step. **The blocker is the
   MCLK1 -EBUSY resume bug, not the hold.**
2. **Venus is powered from probe to unbind, by design** — patch `0198`
   (`pm_runtime_forbid()` on `IS_V3`), because msm8998 venus firmware survives no
   power collapse. Its own ponytail comment names this ceiling. The trade is
   correct; the number was just never measured. Now it is.
3. **Easel (Intel Monette Hill) is powered at `bcm15602` probe and never drops.**
   `bcm15602 4-0008: Easel powered (rails up, soc_pwr_good)` at t=1.79 s, then
   `easel-mipi` configures two D-PHY transmitters (`RX0->TX0 at 1368 Mbps/lane`,
   `RX1->TX1 at 648 Mbps/lane`) that stay configured for the life of the boot.
   The vendor does the opposite: `mnh-sm` powers Easel **down** and brings it up on
   demand, and thermal-engine throttles it from 48 C skin.

**What this rules out** —
- *"The idle drain is all the missing RPM handshake."* The floor after parking is
  still ~207 mA and that part IS the handshake — but a fifth of screen-off idle
  sits above it and is ordinary driver lifecycle work.
- *"The camera costs nothing while no camera app is running."* It costs the
  largest share of the 244 mW, every second of every boot.
- *"`0198`'s idle cost is theoretical."* It is measurable and it is now measured.
  Leave `0198` alone anyway — the alternative is a decoder that does not work
  ([[venus-decode-works-and-what-it-took]]) — but revisit it if the vendor PC
  contract is ever cracked.

**How it was established** — `tools/ph-device.sh --need-booted` for the mutex,
a detached `systemd-run` sampler so ssh was not in the loop during a block, and
the interleave discipline from [[one-arm-cannot-resolve-a-browser-change-here]].
Instrument caveat that bounds every number here: this is **USBIN current, not
battery drain** — BP-00 §M7 stands, an absolute standby figure cannot be taken
over the cable, and the fuel gauge read a constant 8789 uA across every arm.
Deltas are sound; the absolute 1.28 W is a proxy. Overturned by: the same
interleave, unplugged, over WiFi.
