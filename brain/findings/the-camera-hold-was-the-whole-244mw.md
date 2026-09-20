---
id: the-camera-hold-was-the-whole-244mw
title: The 244 mW of always-on idle was the camera sensors alone; venus and Easel are inside the noise
scope: device:google-taimen
subsystem: power
severity: finding
confidence: proven
evidence: taimen kernel 7.2.2 #73, screen blanked (bl_power=4), cabled, full phosh session, detached systemd-run sampler so ssh is out of the loop, n=45 over 90 s: median pmi8998-charger/current_now = 207202 uA, bit-identical to the 2026-09-19 teardown's PARK median, while venus reads runtime_status=active and dmesg still carries 'Easel powered (rails up, soc_pwr_good)'. Teardown BASE was 253247-257851 uA on the same instrument.
refutes: the 244 mW idle cost is shared across the camera sensors, venus and Easel; powering Easel down on demand is worth chasing for idle drain; patch 0198's venus pm_runtime_forbid has a measurable idle cost
first-learned: 2026-09-20
---

**The question** — [[three-always-on-blocks-cost-244mw-at-idle]] measured
48.3 mA (~244 mW) of screen-off idle coming from three blocks nothing was
using: both camera sensors pinned runtime-active from probe, venus powered by
design (patch 0198), and Easel powered at `bcm15602` probe. It did not
apportion the cost between them. Which one is it, and is the Easel work --
powering an in-line MIPI bridge down and back up around camera use -- worth the
risk to a working camera?

**The answer** — **it was the cameras, all of it.** Dropping the
`pm_runtime_get_noresume()` that `imx179_probe()` and `imx362_probe()` never
released puts idle at the PARK floor **while the other two blocks are still
powered**:

| | median `pmi8998-charger/current_now` |
|---|---|
| teardown BASE, nothing parked | 253 247 – 257 851 uA |
| teardown PARK, all three parked | **207 202 uA** (identical both rounds) |
| cameras idling, venus + Easel still up | **207 202 uA** (n=45, 90 s) |

Bit-identical to PARK, on the same instrument, with
`cc00000.video-codec/power/runtime_status` reading `active` and dmesg still
carrying `Easel powered (rails up, soc_pwr_good)`. Venus and Easel together
are inside the 4.6 mA baseline spread, i.e. under ~23 mW combined.

The hold also was not needed. It existed to dodge `clk_prepare_enable()` on
MCLK1 returning `-EBUSY` once CAMSS_TOP has collapsed, and `imx179_power_on()`
already solves that directly -- it holds the CCI device, which owns CAMSS_TOP,
awake across the sensor's whole power cycle. The comment justifying the hold
had outlived its own fix.

**What this rules out** —

- *"Easel on demand is worth chasing for idle drain."* It is worth at most
  23 mW, shared with venus, against hooking stream start on an in-line MIPI
  bridge that both cameras terminate on. Do not break a working camera for it.
- *"0198's venus `pm_runtime_forbid()` has a measurable idle cost."* Not on
  this instrument it does not. The trade stands as written.
- *"The idle floor needs the three blocks dealt with together."* One of them
  carried the whole number.

**SETTLE FIRST, or you will measure BASE and conclude nothing changed.**
Re-measured on the final packaged kernel: a sample started 50 s after boot
gives a median of **253 247 uA** -- indistinguishable from the BASE arm above --
while the same phone, same boot, sampled from 10 minutes' uptime reads
**211 806 uA** (min 207 202, i.e. one 4604 uA ADC code off PARK). The first two
or three minutes after boot are NetworkManager, the session coming up and the
indexers, and they are worth ~45 mA on their own. Every idle number here is
after a ten-minute settle.

**How it was established** — a detached `systemd-run` sampler, so ssh is not
in the loop during the block (an interactive sample shows 200 mA spikes from
the session itself), screen blanked, cabled, full phosh session, kernel
7.2.2 #73. Instrument caveat unchanged and load-bearing: this is USBIN, a
RELATIVE instrument with a ~4.6 mA floor -- fine for a 48 mA delta, useless
for the 10-20 mA userspace items below it. Those need an unplugged capacity
delta.

Overturned by: the same comparison unplugged, or a BASE arm re-measured on
this kernel rather than taken from the earlier session.
