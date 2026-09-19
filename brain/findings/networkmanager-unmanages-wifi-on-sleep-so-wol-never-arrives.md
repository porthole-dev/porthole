---
id: networkmanager-unmanages-wifi-on-sleep-so-wol-never-arrives
title: NetworkManager tears down wlan0 on PrepareForSleep, so wake-on-WLAN only works on suspend paths logind never hears about
scope: device:google-taimen
subsystem: wifi
severity: finding
confidence: proven
evidence: "taimen, kernel 7.2.2 #67. Journal one second before a real idle suspend: `device (wlan0): state change: disconnected -> unmanaged (reason 'unmanaged-nm-disabled')`, then `device (wlan0): reset MAC address to 02:00:2F:8E:8A:4F (unmanage)`, then `wpa_supplicant: nl80211: deinit ifname=wlan0`, then `Reached target Sleep`. A magic packet to the phone's broadcast address during that suspend got no answer at all, twice, with the MAC verified unchanged (6a:40:df:91:21:ae, NM's `stable` cloned address, identical across reboots). After `nmcli c modify <conn> 802-11-wireless.wake-on-wlan magic` the property reads `0x8 (magic)` instead of `0x1 (default)`, no unmanage line appears at suspend, and the same packet woke the phone in 1 s with pm_wakeup_irq=132 (WLAN_CE_2, the IRQ ath10k_snoc_hif_suspend() arms) while the RTC safety alarm was still pending in the future. Neither `[connection] wifi.wake-on-wlan=magic` nor `[connection] 802-11-wireless.wake-on-wlan=magic` in /etc/NetworkManager/conf.d has any effect: the property still reads `0x1 (default)` after `nmcli general reload conf`, checked both ways."
refutes: "wake-on-WLAN works on this device in general; arming WoWLAN in the kernel is sufficient; the earlier WoL result generalises to a normal suspend; wifi.wake-on-wlan can be shipped as a NetworkManager conf.d default; a magic packet failing means the MAC was wrong or randomised"
first-learned: 2026-09-19
---

**The earlier WoL result was real but did not generalise, and this is the
qualification.** It was measured with
`PORTHOLE_SUSPEND_CMD='systemctl start systemd-suspend.service'`, which starts
the unit **directly**. logind never emits `PrepareForSleep`, so NetworkManager
never learns a suspend is happening, the association survives, and the magic
packet lands.

A real suspend -- idle, or `systemctl suspend` -- goes through logind, and one
second before the kernel enters s2idle:

```
device (wlan0): state change: disconnected -> unmanaged (reason 'unmanaged-nm-disabled')
device (wlan0): reset MAC address to 02:00:2F:8E:8A:4F (unmanage)
wpa_supplicant: nl80211: deinit ifname=wlan0
Reached target Sleep
```

There is nothing left to wake. The kernel's WoWLAN is still armed -- `iw phy0
wowlan show` reports magic-packet and disconnect throughout -- and it is
irrelevant, because the interface it was armed on has been torn down and even
had its MAC changed underneath it.

**The fix is one per-connection property, and it cannot be shipped as a
default.**

```
nmcli connection modify <conn> 802-11-wireless.wake-on-wlan magic   # 0x1 (default) -> 0x8 (magic)
```

With it set, no unmanage line appears at suspend and the packet wakes the
phone in **1 s**, `pm_wakeup_irq=132`. Both conf.d spellings were tried and
neither reaches the property -- `[connection] wifi.wake-on-wlan=magic` and the
long `802-11-wireless.wake-on-wlan=magic` both leave it at `0x1 (default)`
after a config reload. So the device package ships a dispatcher
(`90-taimen-wowlan`) that sets it on the connection the first time a wifi
device comes up, guarded on the current value because modifying a connection
re-triggers the dispatcher.

**Two things this kills.** A magic packet that fails is not evidence of a
wrong or randomised MAC -- NM's cloned address here is `stable` and was
byte-identical across every reboot of the session. And `iw phy0 wowlan show`
reporting "enabled" is not evidence that WoL can work; it survives the very
teardown that makes it useless.
