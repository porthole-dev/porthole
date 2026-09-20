---
id: the-slpi-needs-the-irq-not-just-the-mux
title: The SLPI needs to OWN the touch interrupt, not just the i2c mux, before it will report a wake gesture
scope: device:google-taimen
subsystem: input
severity: finding
confidence: proven
evidence: taimen 7.2.2, 2026-09-20. With ftm4.handover=1 (mux to the SLPI) and qcom-smgr-double-tap subscribed, an operator tapping against a positive control got NOTHING, twice, with the AP's ftm4 interrupt provably quiet. The subscription was accepted: instrumenting qcom_smgr_request_buffering_item() printed 'buffering ADD id=243 dt=0 native_count=1 max_rate=1 chosen=1 report_rate=65536' and resp.result was 0. Changing handover from disable_irq() to devm_free_irq() made the same test pass on the first try: 'RESULT the SLPI reported it: qcom-smgr-double-tap type=1 code=143 value=1', with ap_ftm4_irq reading -1, i.e. the line gone from /proc/interrupts entirely. ftm4_enable_sensor() in slpi_v2.mbn has a second failure exit after its tlmm 75 check, 'failed to register ftm4 signal on GPIO [%d]'
refutes: handing the i2c mux to the SLPI is enough to arm a wake gesture; ftm4.handover was tried and the SLPI reported nothing so the SLPI path is dead; double-tap on a blanked screen needs the AP to arm the gesture
first-learned: 2026-09-20
---

**The question** — double-tap-to-wake on a blanked but awake phone is the SLPI's
job: it runs its own ftm4 driver, arms the wake gesture itself and reports the
tap over SMGR. `ftm4.handover` hands it the i2c mux, exactly as
`ftm4_suspend()` does. Why did it still report nothing?

**The answer** — because the mux is only the first of two gates.
`ftm4_enable_sensor()` in `slpi_v2.mbn` checks
`ftm4_i2c_switched_to_slpi()` (tlmm 75 high, polled 50 x 10 ms) and then tries
to register its own handler on the same pin, failing with

```
failed to register ftm4 signal on GPIO [%d]
```

`ftm4.handover` used `disable_irq()`, which **masks a line the AP still owns**.
The SLPI cannot register against that. Swapping it for `devm_free_irq()` made
the identical test pass on the first attempt.

**The two runs, same boot, one line of difference** —

| handover does | AP irq while handed over | result |
|---|---|---|
| `disable_irq()` | count frozen, line still listed | nothing, twice, with an operator tapping |
| `devm_free_irq()` | **absent from `/proc/interrupts`** | `qcom-smgr-double-tap type=1 code=143 value=1` |

`code=143` is `KEY_WAKEUP`. The line disappearing rather than merely going quiet
is the tell that distinguishes a release from a mask, and it is the thing to
check first if this ever regresses.

**What this rules out** —

- "Handing over the mux is enough." It is necessary and was never sufficient.
- "`ftm4.handover` was tried and the SLPI said nothing, so the SLPI path is
  dead." That null was real and it was the mask, not the path.
- "The AP must arm the gesture for a blanked screen." It must not; downstream's
  AP driver never arms one. It stops sensing, mutes the controller's interrupt,
  gives up the mux -- and, per this finding, the interrupt too.
- "The subscription must have failed." It did not:
  `buffering ADD id=243 dt=0 native_count=1 max_rate=1 chosen=1
  report_rate=65536`, with `resp.result` 0. A well-formed ADD at 1 Hz. Note
  that SMGR acking a request proves nothing about whether it will report --
  `qcom_smgr.c` already documents an ack-then-silence failure mode for two data
  types in one request.

**The other half, easily forgotten** — the gesture is not subscribed by default.
`qcom_smgr`'s `write_event_config()` is the IIO `events/in_index0_change_en`
attribute, and it reads 0 until something writes it. Both halves are required:
handover without the subscription is silent, and the subscription without the
handover is silent.

**How it was established** — `ph-slpi-dt2w-probe.py`, which is two-phase on
purpose: with the bus handed over the AP is deliberately deaf, so there is no
way to tell "the SLPI said nothing" from "nobody touched the glass". Phase 1
keeps the bus and waits for a touch as the positive control; phase 2 hands over
and watches `qcom-smgr-double-tap`. It would be overturned by a run where the
line is provably released and the SLPI still reports nothing -- in which case
look for a third gate, starting at `"framework interrupt service not enabled"`,
the other error string next to this one in the firmware.
