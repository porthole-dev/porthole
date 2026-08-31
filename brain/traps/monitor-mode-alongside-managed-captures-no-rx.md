---
id: monitor-mode-alongside-managed-captures-no-rx
title: A monitor vif on ath10k can be added alongside managed but receives nothing
scope: soc:msm8998
subsystem: radio
severity: trap
confidence: proven
evidence: taimen 2026-08-31: mon0 up, rx_packets=0 over 20s, TX frames still delivered
first-learned: 2026-08-31
---


**On ath10k you can add a monitor interface next to a managed one, and it will
receive nothing.** `iw phy0 info` says

```
software interface modes (can always be added):
	 * monitor
```

so `iw dev wlan0 interface add mon0 type monitor` succeeds, `mon0` comes up,
and a capture on it runs happily and reports zero frames. Measured on taimen
2026-08-31: **`/sys/class/net/mon0/statistics/rx_packets` = 0** over a 20-second
capture with an AP beaconing at -58 dBm two metres away.

**What it does still give you is your own transmitted frames.** mac80211 feeds
TX frames to monitor vifs through the tx-status path, so a capture on `mon0`
*is* a reliable way to read exactly what the driver put on the air -- which is
how the 225-byte association request in
[[ap-refuses-us-our-assocreq-is-clean]] was decoded. It is a TX tap, not
a sniffer.

**Why this is a trap and not a limitation:** every frame you were hoping to see
is inbound. "The AP never answered" and "our monitor cannot hear the AP" produce
identical evidence, and the first is a conclusion about the network while the
second is a broken instrument. A whole session can be spent reasoning about an
absence that was never observable.

**So validate the instrument before you trust a null.** Read `rx_packets`, or
count beacons -- something you *know* is on the air. If it is zero, your
capture has no opinion about what the AP did. This is
[[evidence-discipline]] rule 4 with a concrete face on it: *can my instrument
even see what I say is absent?*

To actually capture RX you need a second radio; the phone's own is not
available for it while associated.
