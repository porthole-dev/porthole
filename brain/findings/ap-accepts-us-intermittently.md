---
id: ap-accepts-us-intermittently
title: TEST-SSID does accept this client -- intermittently, with the stock frame
scope: device:google-taimen
subsystem: radio
severity: finding
confidence: proven
evidence: kernel RX AssocResp status=0 aid=2 on 2026-08-31; eight sweeps, ~170 pinned attempts, 3 acceptances all inside one window
refutes: status 18 is a deterministic refusal of our association request; taimen has never associated to TEST-SSID; the VHT Supported Channel Width Set / Extended NSS BW encoding is why the AP refuses; the locally-administered MAC bit is why the AP refuses; an HT or VHT capability or MCS difference is why the AP refuses; PMF or the MFPC bit is why the AP refuses; the interval between association attempts is why the AP refuses
first-learned: 2026-08-31
---

**The question** — the phone is answered `status=18`
(`ASSOC_DENIED_UNSUPP_RATE`) by the UniFi AP while a laptop on the
same BSSID is accepted. Three sessions read that as "something in our
association request is wrong" and went looking for the element responsible.

> **SUPERSEDED 2026-08-31 by [[the-reserved-vht-width-pair-is-why-the-ap-refused]].**
> The central conclusion here -- that our association request is clean and the
> refusal is the AP's -- is **wrong**. ath10k was advertising a VHT Supported
> Channel Width Set / Extended NSS BW pair that 802.11 marks reserved; clamping
> it in the driver gives 8/8 cold associations and a real DHCP lease. VHT looked
> exonerated only because the wpa_supplicant knob used to test it does nothing
> ([[vht-capa-overrides-cannot-touch-channel-width]]). Keep this note for its
> method -- the BSSID pin, the beacon decode, the counterbalancing -- not for
> its verdict.

**The answer** — **there is nothing to find in the frame. The AP accepts this
client, with the completely unmodified stock request, some of the time.**

    [ 3644.852896] wlan0: RX AssocResp from <ap-ch36> (capab=0x1511 status=0 aid=2)
    [ 3644.949384] wlan0: associated
    [ 3648.899179] wlan0: deauthenticated (Reason: 15=4WAY_HANDSHAKE_TIMEOUT)

That is the kernel, not a supplicant guess. The handshake timed out because
the probe deliberately used a dummy PSK -- the association verdict lands
before the 4-way, which is the whole point of testing with a fake key. Three
runs in one sweep got in; two of them carried *modified* frames and the third
carried the stock one, which is the point: the variant did not decide it.

## What this rules out

- **`status=18` is not a rate defect and not deterministic.** The same bytes
  are refused, then accepted, minutes apart. No frame-content theory survives
  a control that succeeds unmodified.
- **The VHT capability encoding is not it.** Our VHT Capabilities Info is
  `0x738139fa`: Supported Channel Width Set = 2 with Extended NSS BW Support
  = 1, which 802.11 Table 9-250 marks **reserved** -- `iw phy0 info` prints
  `Supported Channel Width: (reserved)` -- while the accepted laptop sends the
  valid (1, 0). It looked like the answer. It is not: overriding those bits
  from wpa_supplicant (`vht_capa=0x4 vht_capa_mask=0xc000000c`, verified
  parsed and applied) gave `status=18` on all 12 counterbalanced attempts.
- **The locally-administered MAC bit is not it.** taimen has no factory WLAN
  MAC ([[taimen-has-no-factory-wlan-mac]]), so every address it has ever used
  is a random LAA while the laptop's is OUI-registered -- the one difference
  nobody had varied, because the earlier "MAC randomisation ruled out" only
  swapped one LAA for another. Nine attempts across LAA, a Google OUI and a
  third registered OUI: `status=18` every time.
- **PMF is not it.** NetworkManager defaults `ieee80211w` to optional and so
  sets MFPC in the RSN IE, while a minimal supplicant config leaves it clear --
  and this AP's own beacon advertises RSN capabilities `0x0000`, i.e. no PMF at
  all. `nopmf` / `ieee80211w=1` / a full NM-shaped block (`proto=RSN`,
  `pairwise=CCMP`, `group=CCMP`, `proactive_key_caching=1`) rotated in one
  window: 0 accepted out of 10 each, `status=18` throughout.
- **The gap between attempts is not it.** All three acceptances happened to
  follow an attempt that put no association request on the air, which looked
  like a rate limiter releasing. Tested directly, paired in one window: a COLD
  attempt with 90 s of enforced radio silence before it (interface down, not
  merely idle) against a HOT one 5 s later. 0/8 and 0/8, all `status=18`. The
  correlation was coincidence.
- **No HT or VHT capability or MCS knob is it.** `disable_ht`, `disable_vht`,
  `disable_ht40`, `disable_sgi`, `disable_ldpc`, `disable_max_amsdu`,
  `ampdu_density`, `ht_mcs` (1 stream), `vht_rx/tx_mcs_nss_2` -- swept
  counterbalanced. None separates from the control, and the control itself
  both fails and succeeds.

## Two real frame defects found along the way, neither causal

Both are upstream ath10k, both worth fixing on their own merits:

1. `ath10k_create_vht_cap()` does `vht_cap.cap = ar->vht_cap_info` and never
   validates it, so the WCN3990 firmware's reserved Supported Channel
   Width Set / Extended NSS BW pair goes straight onto the air.
2. `ath10k_get_ht_cap()` sets `IEEE80211_HT_CAP_DSSSCCK40` unconditionally,
   advertising DSSS/CCK in 40 MHz on a 5 GHz association where it is
   meaningless. The accepted laptop does not set it.

## How it was established

Six sweeps, 67 direct `wpa_supplicant` runs plus ~14 NetworkManager
activations, **BSSID pinned to `<ap-ch36>` throughout** -- an unpinned
run tests nothing ([[ap-refuses-us-our-assocreq-is-clean]]). Variant
order was rotated every repetition after the first sweep, because the first
one put the control first in every rep and could not have told a variant
effect from a position effect.

**The successes clustered, and nothing since has reproduced them.** All three
landed inside one sweep of 14 runs. Everything before, and roughly **156
consecutive pinned attempts after** -- 60 NetworkManager activations, 30 PMF
variants, 16 paired cold/hot attempts -- returned `status=18`. So the honest
model is not "each attempt has a ~5% chance" but **"the AP admitted clients
during one window and has refused continuously outside it"**. What opens a
window is not visible from the client, and the two mechanisms that would have
been visible (attempt spacing, per-MAC exclusion) were both tested and are
both refuted above.

**What would overturn this:** a run of several hundred pinned attempts with
the stock frame that never once returns `status=0`. One acceptance is enough
to kill a deterministic-refusal theory; only a long clean null could bring it
back.
