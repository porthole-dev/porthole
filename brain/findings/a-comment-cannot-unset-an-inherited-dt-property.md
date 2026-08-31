---
id: a-comment-cannot-unset-an-inherited-dt-property
title: A board file that declines to mention a DT property does not unset it, and a comment is not a revert
scope: soc:msm8998
subsystem: kernel
severity: finding
confidence: proven
evidence: running /proc/device-tree; msm8998.dtsi:4932 from local commit 737abcabe97b; RAM-boot verification 2026-08-31
refutes: qcom,no-msa-ready-indicator is not set on wahoo; the MSA-ready decision documented in wahoo.dtsi is in force; the board file's comment describes the running configuration; the msa_ready log lines are harmless noise
first-learned: 2026-08-31
---

**The question** — every boot logged a contradiction that nobody chased:

```
ath10k_snoc 18800000.wifi: qmi not waiting for msa_ready indicator
ath10k_snoc 18800000.wifi: qmi unexpected msa_ready indicator
```

The host says it is not waiting for the firmware's MSA-ready indication, and
then the firmware sends it and the host calls it unexpected. Both lines, every
boot, for a month.

**The answer** — `qcom,no-msa-ready-indicator` was set, and the board file that
believed it had opted out had not.

`msm8998-google-wahoo.dtsi` carried a careful comment: *"Deliberately NOT
setting qcom,no-msa-ready-indicator. This firmware does send the MSA_READY
indication - we see it arrive - so honouring it is correct."* It even explained
why the earlier theory had been wrong.

But the property is set on the **SoC** node in `msm8998.dtsi`, by local commit
`737abcabe97b` -- not upstream, this tree's own. A board `.dtsi` that simply
does not mention a property inherits it. **Omitting is not unsetting**; that
needs `/delete-property/`, or the property not being set above you. The
documented decision was never once in force, and `/proc/device-tree` said so
the whole time:

```sh
$ ls /proc/device-tree/soc*/wifi*/ | grep no-msa-ready-indicator
qcom,no-msa-ready-indicator
```

**Why it is not cosmetic.** `ath10k_qmi_event_msa_ready()` is not a
notification handler. It is:

```c
	ath10k_qmi_fetch_board_file(qmi);      /* board-2.bin */
	ath10k_qmi_bdf_dnld_send_sync(qmi);    /* push the BDF to the firmware */
	ath10k_qmi_send_cal_report_req(qmi);   /* calibration report */
```

With the quirk set, `qmi.c` runs all of that immediately after server-arrive
instead of when the firmware signals its shared memory region is ready -- and
then discards the real indication when it arrives. So the board data and
calibration were being pushed to a firmware that had not said it was ready,
on every boot, on a device whose WiFi is intermittently unwell.

**What this rules out**

- *"The property is not set on wahoo."* It is, by inheritance. Check
  `/proc/device-tree`, not the board file.
- *"The comment describes the running configuration."* It described an
  intention. Nothing enforced it, and nothing tested it.
- *"Those two log lines are noise."* They are the driver announcing both
  halves of a contradiction. A message that says *unexpected* is the firmware
  disagreeing with a DT property about its own behaviour.

**The fix** -- revert the local commit; `linux-ws` `20136ecbe34b`. Verified by
RAM-booting a repacked boot image, control first:

| | before | after |
|---|---|---|
| `no-msa-ready-indicator` in `/proc/device-tree` | present | **absent** |
| `qmi not waiting for msa_ready indicator` | every boot | **gone** |
| `qmi unexpected msa_ready indicator` | every boot | **gone** |
| `wcn3990 hw1.0` probe | ok | ok |

**Still open: whether this was causing anything.** The mechanism is real and
the ordering is now correct, but no WiFi symptom has been tied to it. It is
grouped with [[nothing-polls-an-idle-link-on-ath10k]] and
[[two-thirds-of-associations-never-get-keys]] as one of several corrected
mainline/vendor divergences whose individual contribution is not yet separated.

**The generalisable half**, which is why this is filed as a finding and not a
taimen fact: when a board file's comment explains why something is *not* set,
check `/proc/device-tree` before believing it. A comment is not a revert.
