---
id: ap-refuses-us-our-assocreq-is-clean
title: TEST-SSID refuses this client, and our association request is not the reason
scope: device:google-taimen
subsystem: radio
severity: finding
confidence: proven
evidence: mac80211 knob sweep 2026-08-31 with BSSID pinned; 84 assoc attempts at 5180 MHz; open hotspot with SpectrumMgmt set
refutes: status 18 means our rates are wrong; the Supported Channels element or 802.11h content is why 5 GHz association fails; the Power Capability of 30 dBm is why an 802.11h AP refuses us; the SpectrumMgmt capability bit distinguishes the APs that accept us; MAC randomisation explains the refusal; taimen has never associated on 5 GHz
first-learned: 2026-08-31
---

**The question** — the phone cannot join `TEST-SSID`, a Ubiquiti
UniFi network, while every other AP works. The AP answers
`status=18` = `ASSOC_DENIED_UNSUPP_RATE` ("you do not support all the rates in
my BSSBasicRateSet"). Two sessions in a row read that literally and went
looking for a rate, regulatory or 802.11h defect on our side.

> **SUPERSEDED IN PART (2026-08-31, same day).** Two claims below are now
> refuted by [[ap-accepts-us-intermittently]]: that it "has never once
> associated", and the framing that the AP simply refuses us. The kernel has
> since logged `RX AssocResp ... status=0 aid=2` from `<ap-ch36>` with the
> **stock, unmodified** frame. Everything else here stands, and the central
> conclusion stands harder than before: the refusal does not track the frame.

> **SUPERSEDED 2026-08-31 by [[the-reserved-vht-width-pair-is-why-the-ap-refused]].**
> The central conclusion here -- that our association request is clean and the
> refusal is the AP's -- is **wrong**. ath10k was advertising a VHT Supported
> Channel Width Set / Extended NSS BW pair that 802.11 marks reserved; clamping
> it in the driver gives 8/8 cold associations and a real DHCP lease. VHT looked
> exonerated only because the wpa_supplicant knob used to test it does nothing
> ([[vht-capa-overrides-cannot-touch-channel-width]]). Keep this note for its
> method -- the BSSID pin, the beacon decode, the counterbalancing -- not for
> its verdict.

**The answer** — **our association request is clean, and status 18 is a lie.**
Everything the AssocReq contains has been varied and none of it changes the
outcome. The refusal tracks the AP, not the frame.

## The AP is three BSSIDs and they are not interchangeable

Treating them as one network is what wasted the first session:

| BSSID | freq | chan | typical signal | how it fails |
|---|---|---|---|---|
| `<ap-ch36>` | 5180 | 36 | -63 dBm | the only source of `status=18` |
| `<ap-ch140>` | 5700 | 140 | -74..-81 dBm | silent auth timeout |
| `<ap-ch1>` | 2412 | 1 | -58..-62 dBm | silent auth timeout; once reached assoc, then ignored |

A sweep that does not pin `802-11-wireless.bssid` **tests nothing**: the
supplicant walks all three and the last line in the journal is whichever it
tried last, which is `…af` timing out. That exact mistake produced five
identical "auth timed out" verdicts for five different variants before the pin
was added. Pin the BSSID, and confirm the control still reproduces `status=18`.

## What was varied, with a control, and changed nothing

A `mac80211` built with runtime knobs (`supp_chan_mode`, `pwr_cap_dbm`,
`spectrum_mgmt_off`), BSSID pinned to `<ap-ch36>`:

| variant | result |
|---|---|
| control: stock upstream behaviour | `status=18` |
| Power Capability clamped to 23 dBm (the ETSI UNII-1 limit) | `status=18` |
| Power Capability element omitted | `status=18` |
| SpectrumMgmt capability bit cleared | `status=18` |
| Supported Channels skipping disabled channels | `status=18` |
| Supported Channels element omitted | `status=18` |
| **all three suppressed at once** | `status=18` |

Also varied from userspace, no change: `pmf` on and off (the RSN IE's MFPC
bit), `cloned-mac-address` pinned and random, band and BSSID pins, and
transmit power raised to the regulatory maximum.

## The three controls that make this conclusive

1. **The same channel works.** The home AP `<home-ap-5g>` is on
   **freq=5180 -- channel 36, the same channel as `<ap-ch36>`** -- and has
   accepted this client **38 times**. Same radio, same regdomain, same
   27-entry Supported Channels element, same 30 dBm Power Capability.
2. **A SpectrumMgmt AP works.** An open test hotspot advertising
   `capability: ESS SpectrumMgmt ShortSlotTime RadioMeasure (0x1501)`
   associated first try, `status=0 aid=1`. So emitting the 802.11h elements is
   not what upsets anything.
3. **The rates really do match.** `<ap-ch36>`'s own beacon: `Supported rates:
   6.0* 9.0 12.0* 18.0 24.0* 36.0 48.0 54.0`, i.e. basic = 6/12/24. Our
   AssocReq carries all eight OFDM rates. Decoded from the wire, not assumed --
   and note that nobody had ever looked at *this* BSS's beacon before; both
   earlier sessions read the ch140 sibling's and assumed it generalised.

## The frame, decoded from the air

225 bytes, captured off a monitor vif. Elements: SSID(9), Supported Rates(8 --
6/9/12/18/24/36/48/54), Power Capability(min 0, max 30 dBm), Supported
Channels(54 -- 27 channels incl. 169/173), RSN(20 -- CCMP/CCMP/PSK, caps
0x008c so MFPC=1), HT Caps(26), Extended Caps(10), VHT Caps(12), RM Enabled
Caps(5), Supported Operating Classes(22), WMM(7).

Two cosmetic upstream warts noticed while decoding, neither causal here but
both real: mac80211's Supported Channels builder iterates **every**
`sband->n_channels` with no `IEEE80211_CHAN_DISABLED` filter (there is a
`/* TODO: get this in reg domain format */` right above it), so we advertise
channels 169 and 173 that cfg80211 has disabled; and `chan->max_power` on
ch36 is **30 dBm**, from the ath world regd's rule, which is what the Power
Capability element then declares for a phone.

## What is left, and it is not ours

The failure is **non-deterministic in a way frame content cannot be**: the same
BSSID, the same bytes, returns `status=18` on one attempt and silently drops
the Authentication frame on the next, minutes apart. A frame the AP parses and
rejects deterministically does not sometimes go unanswered instead. Sustained
retrying makes it go quiet entirely, which looks like AP-side client exclusion
or a backoff.

It has **never once associated**: no successful activation anywhere in the
journal and `802-11-wireless.seen-bssids` is empty. A note claiming it
"connected successfully at least once" is unsupported by any log on the device.

**The question for whoever runs that controller**, which is now sharp:

> Does the TEST-SSID WLAN have Minimum RSSI, "Minimum Data Rate Control", a MAC
> filter, or a blocked-client entry? A client that supports every advertised
> basic rate is being answered `status=18`, and the same client associates to
> other APs on the same channel.

Minimum Data Rate Control is the one UniFi feature that refuses with a
*rate* reason while the beacon still advertises low basic rates, which is
exactly the shape of what we see -- but that is a hypothesis about their
config, not a measurement, and it must be labelled as one.
