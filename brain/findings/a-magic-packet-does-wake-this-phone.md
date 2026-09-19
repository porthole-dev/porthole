---
id: a-magic-packet-does-wake-this-phone
title: Wake-on-WLAN works -- a magic packet wakes taimen from s2idle, and the association survives suspend
scope: device:google-taimen
subsystem: wifi
severity: finding
confidence: proven
evidence: "taimen, kernel 7.2.2 #65 (aport r64), on USB for control, WoWLAN armed by /usr/lib/systemd/system-sleep/10-taimen-wowlan via PORTHOLE_SUSPEND_CMD='systemctl start systemd-suspend.service'. Three arms, same alarm (+100 s), same path, wakealarm cleared before each. Controls (no packet): slept 101 s and 101 s, wakeirq=116 = pm8xxx_rtc_alarm. Test: magic packet for 6a:40:df:91:21:ae sent to 192.168.1.255 ports 9 and 7 from the host at epoch 1789844060; SUSPEND_ENTER 1789844016, SUSPEND_RETURN 1789844060 -- the same second as the send, 44 s into a 100 s alarm -- wakeirq=132 = WLAN_CE_2. IRQ map from /proc/interrupts: 130 WLAN_CE_1, 132 WLAN_CE_2, 116 pm8xxx_rtc_alarm. WLAN_CE_2 is ce_irqs[ATH10K_SNOC_WAKE_IRQ] (ATH10K_SNOC_WAKE_IRQ is 2), the one ath10k_snoc_hif_suspend() arms with enable_irq_wake(). link_after reports the same BSSID as link before, with no reassociation. Vendor parity: qcacld-3.0 gEnableWoW defaults to 3 (magic pattern + pattern byte, all interfaces), core/hdd/inc/wlan_hdd_cfg.h:11059."
refutes: "a magic packet does not wake taimen; wake-on-WLAN needs WMI_SERVICE_D0WOW; the WoWLAN win is illusory because the association drops during suspend; the AP will not forward a broadcast to a station in powersave; ath10k on SNOC never programs the wake pattern"
first-learned: 2026-09-19
---

**It works.** Two controls and one test, same kernel, same alarm, same suspend
path, interleaved with the wakealarm cleared each time:

```
control  no packet   slept 101 s of 100 s   wakeirq 116  pm8xxx_rtc_alarm
control  no packet   slept 101 s of 100 s   wakeirq 116  pm8xxx_rtc_alarm
test     magic pkt   slept  44 s of 100 s   wakeirq 132  WLAN_CE_2
```

The return is stamped in the same second the host sent the packet, and the
waking IRQ is the one `ath10k_snoc_hif_suspend()` arms with
`enable_irq_wake()`. That also settles the older question of whether the link
survives: the AP delivered a frame to this station while it was asleep and the
BSSID is unchanged afterwards, with no reassociation.

**Two things that make this look broken when it is not.**

1. **The host's ARP entry for the phone goes `FAILED` while it sleeps.** A
   unicast magic packet then never leaves the host at all. Send to the subnet
   broadcast (`192.168.1.255`), which goes to `ff:ff:ff:ff:ff:ff` and is
   delivered after the DTIM, or prime the neighbour entry first.
2. **`ph-suspend-cycle.sh` used to write `+N` to `wakealarm` without clearing
   a pending one.** The RTC rejects that write, the shell swallows it, and the
   *previous* run's alarm fires instead -- so the cycle ends early at someone
   else's deadline with `wakeirq=<rtc>` and reads as a clean timed wake. It
   cost one arm here before it was fixed; a `+120` arm returned after 44 s,
   exactly on the prior cycle's `+120`.

**Do not add a "wake on any packet" trigger.** `magic-packet disconnect` is
what the shipped hook arms and what these numbers are for. The vendor's own
default is equivalent (`gEnableWoW=3`).
