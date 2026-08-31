---
id: nothing-polls-an-idle-link-on-ath10k
title: ath10k turns off mac80211's idle connection polling, then disables the firmware keepalive too
scope: generic
subsystem: radio
severity: finding
confidence: proven
evidence: linux-ws ath10k mac.c:10132 and 5785; mac80211 mlme.c:123,140,4781,9320; vendor WCNSS_qcom_cfg.ini gStaKeepAlivePeriod=60 read from vendor_b
refutes: mac80211 polls an idle ath10k link; the firmware keepalive is redundant because mac80211 does it; beacon-miss detection covers a dead link; a link that dies will be noticed and torn down
first-learned: 2026-08-31
---

**The question** — the link dies while every layer still reports connected:
`wlan0` UP with an address and a default route, `nmcli` saying connected, and
100% packet loss in both directions for over half an hour. **It does not
self-heal.** What was supposed to notice, and why didn't it?

**The answer** — nothing was watching. Two decisions, each defensible alone,
cancel each other out:

```
mac.c:10132   ieee80211_hw_set(ar->hw, CONNECTION_MONITOR);
```

tells mac80211 the driver handles connection monitoring. mac80211 obeys it in
four places (`mlme.c` 123, 140, 4781, 9320): `ieee80211_sta_reset_beacon_monitor()`
returns without arming `bcn_mon_timer`, `ieee80211_sta_reset_conn_monitor()`
returns without arming `conn_mon_timer`, neither is rescheduled after a probe
response, and the connection is not probed on resume. **`conn_mon_timer` IS the
idle connection polling.**

Then, at `mac.c:5785`, with this comment:

```c
	/* It makes no sense to have firmware do keepalives. mac80211 already
	 * takes care of this with idle connection polling.
	 */
	ret = ath10k_mac_vif_disable_keepalive(arvif);
```

which sends `WMI_STA_KEEPALIVE` with `arg.interval =
WMI_STA_KEEPALIVE_INTERVAL_DISABLE`.

**The comment's premise is false for this driver, because this driver is what
made it false.** mac80211 is not doing idle connection polling here; ath10k
switched it off 4000 lines further down. Read either site alone and it is
reasonable. Read both and nothing polls an idle link.

**What still works, so the gap is narrower than "nothing is monitored"** --
firmware beacon-miss is real and wired up: `ath10k_mac_handle_beacon_miss()`
calls `ieee80211_beacon_loss()`. If the AP stops beaconing, that is caught.
The uncovered case is precise:

> **a dead data path underneath a live beacon is detected by nobody.**

Which is exactly the reported symptom -- beacons keep arriving so the
association stays up and every status field stays green, while nothing gets
through.

**What the vendor does instead.** From the taimen `vendor` partition,
`/firmware/wlan/qca_cld/WCNSS_qcom_cfg.ini`:

```
gStaKeepAlivePeriod=60
```

A 60-second NULL-frame station keepalive, on, in the shipping configuration.
The downstream stack does not rely on the host to poll the link; it arms the
firmware to do it, which is the mechanism mainline explicitly turns off.

**Status: mechanism proven by reading, causation NOT yet proven.** The code
path is certain -- the flag, the four mac80211 sites, the disable call and the
vendor's setting are all quoted above. What has *not* been shown is that this
is what produced the observed failures. That needs a soak in which the link is
genuinely idle, and it is easy to accidentally disprove: **a monitoring probe
is itself a keepalive.** The first version of `tools/tk-wifi-soak.sh` pinged
the gateway every 60 s and would have prevented the bug it was watching for;
it now samples passively and probes only every tenth sample.

**The fix, when it is time.** Keep `CONNECTION_MONITOR` -- firmware beacon-miss
genuinely works and mac80211 polling would duplicate it and cost power. Arm the
firmware keepalive rather than disabling it, which is the combination the
vendor ships. That is a change to `interval` at the one call site, plus a
reason in the comment that is true.
