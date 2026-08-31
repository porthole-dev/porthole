---
id: two-thirds-of-associations-never-get-keys
title: Two thirds of successful associations never complete the 4-way handshake, and ath10k says nothing
scope: device:google-taimen
subsystem: radio
severity: finding
confidence: proven
evidence: boot of 2026-08-30: 92 associations paired against Key negotiation completed; strings on the running ath10k_core.ko; 11 boots of journal
refutes: the ath10k key-install and HTT-drop fixes were lost in the 6.18 to 7.2 rebase; the shipping kernel lacks the key-timeout recovery; the WiFi instability is the key-install-timeout path; wifi instability shows up as ath10k errors in dmesg
first-learned: 2026-08-31
---

**The question** — is this device's WiFi actually stable now that the ath10k
key work has landed, and if not, what is still failing?

**The answer** — it is not stable, and the failure is **silent**. Pairing every
`wlan0: associated` against the `WPA: Key negotiation completed` that should
follow it, over one full day (boot of 2026-08-30):

```
associations:            92
  followed by 4-way:     31
  NOT followed by 4-way: 61
what follows an unpaired association:  deauth 24 · another assoc 20 · reason=4 17
median seconds to that next event:     7.6
```

**Two thirds of successful 802.11 associations never get keys.** They are
abandoned about 7.6 s later and retried, which is why the symptom is reported
as "flaky" rather than "broken" -- roughly one attempt in three sticks. The
earlier boot -7 shows the same shape at smaller scale (8 associations, 1 key
negotiation).

Pair them yourself before trusting any "it works now": a bare count of
`wlan0: associated` looks *healthy* during this failure, because the driver
keeps associating happily. The ratio is the signal, not the count.

**What this rules out**

- *"The ath10k fixes were lost in the 6.18 -> 7.2 rebase."* They were not. Both
  are in the product tree, and `0002` is there in a **better** form than the
  filed patch -- it distinguishes `SET_KEY` (restart the firmware) from
  `DISABLE_KEY` (do not, it is the teardown path and a restart would convert a
  bounded 3 s stall into a multi-second outage). That refinement is why
  `git apply --check --reverse` reports the patch as *not* present; reverse-apply
  answers "is this exact diff in", not "is this fixed".
- *"The shipping kernel does not have them."* It does. The device runs the
  aport build, not the tree, so check the artefact rather than the source:
  `strings` on the running `/lib/modules/$(uname -r)/.../ath10k_core.ko`
  contains `vdev %i install key timed out, restarting hardware`.
- *"This is the key-install-timeout path."* It is not.
  `install key timed out` has fired **zero** times in 11 boots and 8 days of
  journal, and there has been **not one** ath10k firmware crash, restart or
  recovery in the same window. The recovery that ships is real and has simply
  never been reached by this failure.
- *"WiFi instability shows up as ath10k errors."* Across the whole failing
  boot, ath10k logged nothing but its normal probe lines plus a single
  `failed to extract amsdu: -11`. **The driver is silent while two thirds of
  connections fail.** That silence is the actual obstacle.

**What is NOT closed — and the instrument now in place**

The open fork is one question, and nothing in the journal could answer it
because wpa_supplicant at its default level does not record the EAPOL exchange:

- **no `RX EAPOL-Key` at all** -> the AP never sent msg 1/4, and the problem is
  on the receive side or at the AP;
- **msg 1/4 received but no completion** -> our msg 2/4 never left the chip,
  which is the HTT tx-drop path
  ([[wifi-dies-while-still-reporting-connected]] describes exactly this: the
  firmware kept associating while returning a drop completion for every data
  frame).

`/etc/systemd/system/wpa_supplicant.service.d/10-eapol-debug.conf` now runs the
supplicant with `-d`, which logs the EAPOL state machine without hexdumping
every frame, so the journal survives a multi-day soak. **The next occurrence
is diagnosable; previous ones were not.** Revert by deleting that file,
`systemctl daemon-reload`, `systemctl restart wpa_supplicant`.

Next arm: leave it on a known-good AP, then pair the counts again and read the
EAPOL lines around the unpaired associations.
