---
id: the-reserved-vht-width-pair-is-why-the-ap-refused
title: The reserved VHT channel-width pair was the refusal -- clamping it associates 8/8
scope: soc:msm8998
subsystem: radio
severity: finding
confidence: proven
evidence: 2026-08-31 -- ath10k clamp of SUPP_CHAN_WIDTH/EXT_NSS_BW gave 8/8 cold pinned associations status=0, then a real NetworkManager connection with a DHCP lease; prior base rate was 3 acceptances in ~170 attempts
refutes: our association request is clean and status 18 is a lie; the refusal is AP-side policy; Minimum RSSI or Minimum Data Rate Control on the controller explains it; the phone's signal or path loss is the deciding variable; nothing in the frame can explain an intermittent refusal; VHT has been tested and exonerated; the AP must be asked about its configuration
first-learned: 2026-08-31
---

**The question** -- a UniFi AP answered `status=18`
(`ASSOC_DENIED_UNSUPP_RATE`) to this phone while a laptop, and the user's
**Android on the same handset**, associated fine. Four sessions diffed the
association request, proved the Supported Rates element byte-identical to the
accepted laptop's, and concluded the frame was clean and the AP was at fault.
The last one was about to hand the question to a network administrator.

**The answer** -- **it was our frame all along. ath10k advertises a VHT
capability pair that IEEE 802.11-2016 Table 9-250 marks reserved, and this AP
refuses it.** Clamp it and the AP accepts, repeatably.

    ath10k_create_vht_cap():  vht_cap.cap = ar->vht_cap_info;   /* verbatim */

WCN3990 firmware reports Supported Channel Width Set = 2 ("160 and 80+80 MHz")
together with Extended NSS BW Support = 1. That pair is reserved. It is also
untrue of the hardware: the WCN3990 `ath10k_hw_params` entry declares no
`vht160_mcs_rx_highest` / `vht160_mcs_tx_highest` at all, so the MCS set
simultaneously says "not 160-capable". The frame contradicts itself, and
ath10k passes the firmware word through without ever looking at it.

The fix is to clamp to what the hardware actually declares:

    if (!hw->vht160_mcs_rx_highest && !hw->vht160_mcs_tx_highest)
            vht_cap.cap &= ~(IEEE80211_VHT_CAP_SUPP_CHAN_WIDTH_MASK |
                             IEEE80211_VHT_CAP_EXT_NSS_BW_MASK);

    before: VHT Capabilities (0x738139fa)  Supported Channel Width: (reserved)
    after:  VHT Capabilities (0x338139f2)  Supported Channel Width: neither 160 nor 80+80

## The result, counted rather than asserted

Acceptance here was known to be intermittent -- 3 acceptances in ~170 pinned
attempts with the stock frame, all clustered in one window
([[ap-accepts-us-intermittently]]). So one success proves nothing. With
the clamp, cold pinned attempts each with a **fresh MAC**:

    attempt 1..8  ->  ASSOCIATED(status=0)
    TOTAL accepted=8 refused18=0 silent=0 of 8

then a real NetworkManager activation with the real PSK: 4-way completed,
`inet <lan-ip>/24`, traffic flowing. Under the old base rate 8/8 is on the
order of 1e-14.

## Why four sessions ruled VHT out and were wrong

Because the knob they tested with does nothing.
[[vht-capa-overrides-cannot-touch-channel-width]] is the whole story:
wpa_supplicant's `vht_capa` / `vht_capa_mask` are parsed, logged as applied,
and then **silently dropped** by mac80211, whose
`ieee80211_apply_vhtcap_overrides()` has no case for either field. Every one
of those twelve "counterbalanced attempts" transmitted the identical reserved
bytes. The negative result looked like evidence and was noise.

**The lesson, and it is the expensive one:** a variant that cannot be shown to
differ from the control ON THE AIR is not a variant. Capture the frame.

## Downstream does not do this, which is why Android works

qcacld-3.0 never puts the firmware word on the air. It assembles the VHT
capability field by field from its own CFG store and uses the firmware's
capabilities only to clamp *downward*:

- `parser_api.c:906` -- forces `supportedChannelWidthSet = 0` whenever the
  session is below 160 MHz, which is every ordinary 80 MHz association
- `parser_api.c:1040` -- `pDot11f->reserved1 = 0`, hard-zeroing bits 30-31;
  Extended NSS BW does not exist as a concept in that driver
- `wlan_hdd_main.c:1495` -- init value on WCN3990 is `VHT_CAP_80_SUPP` (0)

So Android emits `(0, 0)` and mainline emitted `(2, 1)`, on the same chip with
the same firmware. **The user's working Android on the same handset was the
control that broke the case open** -- it made "the AP is at fault" untenable
and forced the search back onto our own frame.

## The second ath10k wart, still unfixed

`ath10k_get_ht_cap()` sets `IEEE80211_HT_CAP_DSSSCCK40` unconditionally,
advertising DSSS/CCK in 40 MHz on 5 GHz where it is meaningless. Downstream
leaves it clear (`WNI_CFG_HT_CAP_INFO` default 0x016C, bit 12 clear). Not
causal here -- the clamp alone gives 8/8 -- but it is real and worth fixing.

**What would overturn this:** a long run of cold pinned attempts WITH the
clamp that returns `status=18`. One would reopen it.

## How it was established

`tools/wifi-mgmt-capture.py` on a monitor vif for every frame; each variant
confirmed on the air before being believed. Module pushed on the `mod` rung
(see [[a-tree-built-module-carries-btf-the-running-kernel-rejects]] -- the
rung does NOT strip BTF, and the load fails as "Symbolic link loop").
