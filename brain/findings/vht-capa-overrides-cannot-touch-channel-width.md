---
id: vht-capa-overrides-cannot-touch-channel-width
title: wpa_supplicant's vht_capa cannot change Supported Channel Width Set or Extended NSS BW -- mac80211 drops it
scope: generic
subsystem: radio
severity: finding
confidence: proven
evidence: on-air capture 2026-08-31 -- vht_capa=0x4 vht_capa_mask=0xc000000c parsed and applied by wpa_supplicant, VHT Caps on the air unchanged at fa398173; net/mac80211/vht.c has no case for either field
refutes: the reserved VHT Supported Channel Width Set / Extended NSS BW pair has been tested and exonerated; vht_capa in a wpa_supplicant network block can override any VHT capability bit; a supplicant log line saying the override was applied means it reached the air
first-learned: 2026-08-31
---

**The question** -- a client was advertising a VHT Capabilities Info of
`0x738139fa`: Supported Channel Width Set = 2 with Extended NSS BW Support =
1, a pair IEEE 802.11-2016 Table 9-250 marks **reserved**. To test whether an
AP was refusing us over it, a session set `vht_capa=0x4` /
`vht_capa_mask=0xc000000c` in the wpa_supplicant network block, saw the
override parsed and applied in the debug log, got `status=18` on all 12
counterbalanced attempts, and concluded VHT was not the cause.

**The answer** -- **that experiment could never have worked. The knob does not
reach those bits, and the conclusion drawn from it is void.**

wpa_supplicant accepts the value and passes it down -- the debug log is
honest:

    * vhtcaps      - hexdump(len=12): 04 00 00 00 00 00 00 00 00 00 00 00
    * vhtcaps_mask - hexdump(len=12): 0c 00 00 c0 00 00 00 00 00 00 00 00

and the frame on the air, captured in the same second, is unchanged:

    191 VHTCapabilities  len=12  fa398173 faff0000 faff0000

`ieee80211_apply_vhtcap_overrides()` in `net/mac80211/vht.c` handles a fixed
allowlist and nothing else:

- `__check_vhtcap_disable()` for RXLDPC, SHORT_GI_80, SHORT_GI_160, TXSTBC,
  SU_BEAMFORMER, SU_BEAMFORMEE, RX_ANTENNA_PATTERN, TX_ANTENNA_PATTERN
- the A-MPDU length exponent, decrease only
- the RX/TX MCS maps, decrease only

`IEEE80211_VHT_CAP_SUPP_CHAN_WIDTH_MASK` (0x0000000C) and
`IEEE80211_VHT_CAP_EXT_NSS_BW_MASK` (0xC0000000) appear **nowhere** in
`net/mac80211/` except `debugfs_sta.c`, which only prints them. There is no
code path by which a userspace override changes either field. The masked bits
are silently discarded.

## What this rules out

- **"VHT has been tested."** It has not. Every run of that experiment
  transmitted the identical reserved pair. A control and twelve variants that
  all put the same bytes on the air are one measurement repeated, not a sweep.
- **"The supplicant said it applied the override, so it is on the air."** The
  supplicant said it *passed the attribute down*, which is true and
  irrelevant. Only a capture of the frame settles what was transmitted.

## The general lesson

An override knob that is parsed, accepted, logged and then ignored one layer
down is the most expensive kind of instrument, because every negative result
it produces looks like evidence. Before trusting a sweep of any
`disable_*` / `*_capa` supplicant knob, **capture one frame and confirm the
bytes moved.** If the variant and the control are byte-identical on the air,
the sweep measured nothing -- see [[an-instrument-that-fails-quietly-is-worse-than-none]].

## How it was established

One `wpa_supplicant -d` run with the override set, against a monitor vif
capturing the association request it produced (`tools/wifi-mgmt-capture.py`,
which dumps every element of every AssocReq). Supplicant hexdump and on-air
hexdump compared directly. Then the mac80211 source read end to end for the
two field masks.

**What would overturn it:** a kernel where `ieee80211_apply_vhtcap_overrides()`
gained handling for those masks -- check the function before reusing this.

## What to do instead

Change it in the driver. For ath10k the value is taken verbatim in
`ath10k_create_vht_cap()` (`vht_cap.cap = ar->vht_cap_info;`) with no
validation, so a clamp there is the only way to vary those bits -- a module
rung, not a userspace edit.
