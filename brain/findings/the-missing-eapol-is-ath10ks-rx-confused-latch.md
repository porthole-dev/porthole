---
id: the-missing-eapol-is-ath10ks-rx-confused-latch
title: "The missing 4-way handshake is ath10k's rx_confused latch: one split A-MSDU disables data RX for the life of the firmware"
scope: device:google-taimen
subsystem: radio
severity: finding
confidence: proven
evidence: eight days of journal, nine "failed to extract amsdu" events paired against key negotiations; one boot of 69 associations / 2 handshakes / 0 RX EAPOL-Key; a modprobe cycle that restored the link on the first association
refutes: the AP is refusing us; it is the channel, the regulatory domain, or channel 13 being no-IR; it is band specific; it is the key-install-timeout path; wifi instability shows up as ath10k errors; the ath10k key fixes were lost in the rebase
first-learned: 2026-09-01
---

**The question** — [[two-thirds-of-associations-never-get-keys]] proved that
most successful associations never get a 4-way handshake, and left exactly one
fork open: does the AP's EAPOL msg 1/4 never arrive, or does our msg 2/4 never
leave the chip? The wpa_supplicant `-d` instrument was installed to answer it.

**The answer** — msg 1/4 never arrives, and the reason is in our own driver.
Counted on the failing boot of 2026-09-01:

```
associations:          69
key negotiations:       2
RX EAPOL-Key frames:    0
IEEE 802.1X RX:         4      (the two that worked, msg 1/4 and 3/4)
```

`ath10k_htt_rx_in_ord_ind()` pops an in-order indication into an on-stack list
and hands it to `ath10k_htt_rx_extract_amsdu()`. The WCN3990 may split one
A-MSDU across **two consecutive indications**; the extractor then runs off the
end of the list without finding a last-MSDU marker and returns `-EAGAIN`. The
switch treats that as impossible:

```c
		case -EAGAIN:
			fallthrough;
		default:
			/* Should not happen. */
			ath10k_warn(ar, "failed to extract amsdu: %d\n", ret);
			htt->rx_confused = true;
```

`rx_confused` is cleared in **exactly one place**, `ath10k_htt_rx_alloc()`, so
only a firmware restart ever clears it. While set, both the handler and
`ath10k_htt_rx_in_ord_ind()` return `-EIO` at their first line. **All data RX
is dead from that moment until the module is reloaded.**

Management frames go through WMI and are untouched, which is what makes this so
hard to see: the station keeps associating perfectly. Only data is lost, and
the first data frame of any connection is EAPOL msg 1/4 -- so every association
from then on reaches ASSOCIATED and times out. ath10k logs one warning, ever.

**The correlation, over eight days.** Nine `failed to extract amsdu: -11`
events. **Not one is followed by a successful key negotiation within nineteen
minutes.** Five were ended only by a reboot; four "recovered" after 19 minutes
to 9 hours, which is the AP or the supplicant giving up and something else
restarting the firmware. The decisive arm:

```
modprobe -r ath10k_snoc && modprobe ath10k_snoc
  -> 1 association, keys completed in the same second
```

**What this rules out**

- *The AP.* During the failure the supplicant alternated 5 GHz (5180) and
  2.4 GHz (2472) and **associated successfully on both, got keys on neither**.
- *The channel or the regulatory domain.* phy0 sits at `country 99` by design --
  that is patch 0056's `regd_override=0x67` world SKU doing its job, and the
  country is meant to come from the beacon country IE. Beacon hints were
  confirmed to have cleared no-IR on channels 12-13, and association still
  failed. Do not spend a session on the regdomain for this symptom.
- *A band or a signal problem.* Both bands, identical behaviour.
- *The key-install-timeout path.* `install key timed out` has never fired.

**The fix** — upstream RFC by Richard Acayan, 2026-02-09, "wifi: ath10k: make
in-order rx amsdu buffers persistent", which moves the list into driver state so
a split A-MSDU completes on the next indication instead of latching. Carried as
aport patch `0202` with his authorship. **It is an RFC with review replies that
have not been read**; read the thread before posting a Tested-by.

**How to check it is really running** — `strings` the module on the phone, not
the source: the patched `ath10k_core.ko` contains
`split amsdu did not resume immediately`. Confirm ath10k is absent from
`modules-initfs.mainline` (it is), so the loaded copy really came from
`/lib/modules` and not the boot image.

**What is NOT yet proven** — the patched path has not been exercised. As of the
first boot on it: 2 associations, 2 key negotiations, 0 amsdu failures. The
proof is the next `failed to extract amsdu`: RX must survive it. Until one
occurs, this fix is installed and verified present, not demonstrated. Pair the
counts again after a few days -- the ratio is the signal, never the count.
