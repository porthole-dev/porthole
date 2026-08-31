---
id: taimen-has-no-factory-wlan-mac
title: taimen invents a new WLAN MAC every boot -- and it is not rmtfs, not caldata, and not a rate mismatch
scope: device:google-taimen
subsystem: radio
severity: finding
confidence: proven
evidence: journal 2026-08-31 08:49-10:05; ethtool -P across a reboot; ath10k qmi.c has no MAC path
refutes: MAC randomisation is ruled out because the permanent MAC was forced; ethtool -P reports a real factory address; the random MAC and empty firmware-version string mean rmtfs is not serving WLAN caldata; taimen has never associated on 5 GHz
first-learned: 2026-08-31
---

**The question** — the phone cannot join `TEST-SSID`, a Ubiquiti
UniFi network, while every other AP works. A previous session concluded the AP
answers `status=18` ("you do not support my basic rates") when we demonstrably
do, ruled out MAC randomisation, regdomain, PMF, HT/VHT and basic rates, and
handed back "it is not ours, ask the network admin".

**The answer** — the phone has **no factory WLAN MAC at all**, and presents a
different locally-administered address on nearly every association. Nothing on
a network side can enrol a client whose identity never repeats.

**This is a real defect and it is NOT why TEST-SSID fails.** That was this
note's original claim and [[ap-refuses-us-our-assocreq-is-clean]]
refutes it: the same random-LAA-MAC scheme associates fine to three other APs,
including one on the identical channel. Read this note for the MAC defect and
that one for the association failure; conflating them is what cost a session.

```
ath10k_snoc 18800000.wifi: invalid MAC address; choosing random
```

Measured across one reboot, with the control run first:

| | before reboot | after reboot |
|---|---|---|
| `ethtool -P wlan0` ("permanent") | `56:61:bd:56:26:94` | `86:c6:a5:48:37:2c` |
| `choosing random` in dmesg | yes | yes |

Both are locally-administered (`0x56`, `0x86` — bit 1 set). **The address
`ethtool -P` calls permanent is the random one.** That is the trap: a test that
"forces the permanent MAC" swaps one random LAA address for another and proves
nothing. That is exactly the test the previous session used to rule MAC
randomisation out.

**Why the driver does this, precisely** — on snoc, mainline ath10k has exactly
one source for a MAC, `device_get_mac_address()` in `core.c` (~3456), which
reads `mac-address` / `local-mac-address` / an nvmem cell from the DT node.
`msm8998.dtsi` `wifi@18800000` carries none, so `eth_random_addr()` runs.
**`ath10k/qmi.c` contains no MAC handling whatsoever** — grep it. So the MAC
cannot be a downstream symptom of QMI, caldata or rmtfs; there is no code path
by which those could supply one. Downstream gets its address from userspace
(the same way Bluetooth gets its BD address from `libbt-vendor`), and mainline
has no equivalent.

Bluetooth has the identical defect: `btmgmt info` reports `02:00:B6:B1:38:94`,
the `hci_qca` placeholder. **Both radios lack their factory addresses.** That
is one drift with two faces, not two bugs.

**What this rules out**

- *"MAC randomisation is ruled out -- we forced the permanent MAC."* See the
  table above. The forced address was `56:61:bd:56:26:94`, itself random and
  locally administered. `ethtool -P` on this device does not report a factory
  address, because there is not one.
- *"The random MAC and the empty firmware-version string mean rmtfs is not
  serving WLAN caldata."* Both halves fail. **rmtfs is running and served
  before the WLAN driver probed** (`taimen-modem-bringup: rmtfs serving after
  0s`); its only failures are `modem_fsg_oem_1/2`, which are modem FSG files.
  The **empty version string is normal for WCN3990**: `firmware-5.bin` is a
  **60-byte stub** for non-BMI targets -- the real firmware is `wlanmdsp.mbn`,
  loaded via the TZ, and its build id *does* arrive over QMI:
  `WLAN.HL.2.2.4-00161-QCAHLSW8998MTPL-1`. Neither "tell" tells you anything
  about caldata, and the random MAC cannot be a caldata symptom at all:
  **`ath10k/qmi.c` contains no MAC handling whatsoever**, so on snoc the only
  source is `device_get_mac_address()` reading the DT.
- *"taimen has never associated on 5 GHz."* It has, routinely --
  **84 association attempts at 5180 MHz and 38 successes** against the home AP
  `<home-ap-5g>`. Counting only "successful connections" from a NetworkManager
  log undercounts, because the 2.4 GHz profile is the one that autoconnects.

**What is NOT closed** — recovering the device's real address. It is not on
the phone (see below), so the stable-MAC fix below is a *substitute* identity,
not the original one. If the phone was enrolled anywhere while it ran Android,
that enrolment names an address we can no longer produce.

**The fix that landed** — `/etc/NetworkManager/conf.d/90-stable-wifi-mac.conf`:

```ini
[connection-wifi-stable-mac]
match-device=type:wifi
wifi.cloned-mac-address=stable
```

NM's default is `preserve`, which inherits whatever the scan randomiser last
set — so the connection MAC was effectively fresh per activation. `stable`
derives it from the connection UUID plus `/var/lib/NetworkManager/secret_key`:
deterministic across reboots, nothing hardcoded. Verified across the reboot in
the table above — the underlying random MAC changed, the connection MAC held at
`<stable-mac>`.

This is deliberately *not* a DT `local-mac-address`: that would hardcode a
per-unit address in a per-model file, which is the same objection already
recorded against doing it for Bluetooth. If a real per-device address is ever
recovered, `device_get_mac_address()` is already waiting for it and ath10k also
supports `nvmem-cells` (`ATH10K_CAL_MODE_NVMEM`) — no driver patch needed
either way.

**Where the address is not** — searched and empty, so nobody repeats it: the
`persist` partition (mounted ro; `display/ sensors/ rfs/ widevine/ time/ audio/
battery/`, no `wifi/`, no `wlan_mac.bin`, no `Intf0MacAddress` anywhere in the
raw device), `devinfo`, the stock DTB dump `blobs/dump-merged.dtb` (vendor node
`/soc/qcom,msm_ath10k_wlan`, no mac property), and `blobs/efs-backup/`
(`fsg.img`, `modemst1.img`, `modemst2.img`, `fsc.img` — no matching strings).
The practical sources left are the UniFi controller's client list from when the
phone ran Android, or Android itself.
