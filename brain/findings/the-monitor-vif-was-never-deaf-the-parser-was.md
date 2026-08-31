---
id: the-monitor-vif-was-never-deaf-the-parser-was
title: The monitor vif was never deaf -- the radiotap parser was, and the phone is LOUD
scope: device:google-taimen
subsystem: radio
severity: finding
confidence: proven
evidence: tools/wifi-mgmt-capture.py run 2026-08-31: 21790 beacons at mean -54.7 dBm, phone TX at -36 dBm, after fixing radiotap Channel-field alignment
refutes: the ath10k/mt76 monitor vif cannot report RSSI; the phone's signal cannot be measured from the client side; the phone is too weak to be heard; the regulatory domain is a lever on this phone's TX power; the refusal is specific to the TEST-SSID WLAN; a MAC filter or blocked-client entry on that WLAN explains it; a sibling WLAN's silence means the sibling behaves differently
first-learned: 2026-08-31
---

**The question** -- the capture rig built to compare the refused phone against
the accepted laptop printed `rssi 0` for every frame, including all 29
beacons. That was read as "the monitor vif is deaf to signal, like ath10k's",
and the signal question -- the last client-side variable
[[ap-accepts-us-intermittently]] left open -- was written off as
unmeasurable without controller access.

**The answer** -- **the vif was fine. The parser had a one-number bug, and
once fixed it answers the signal question directly.**

The throwaway rig's radiotap walker gave field bit 3 (Channel) an alignment of 4.
The spec says 2: it is two u16s, frequency then flags. Every field after it
was then read two bytes late, so the antenna-signal byte came back as
padding -- reliably `0`, which is exactly what a genuinely absent field would
also look like. That is what made it read as a deaf vif.

    current parser offsets: {0: 8, 1: 16, 2: 17, 3: 20, 5: 24}
    radiotap spec offsets : {0: 8, 1: 16, 2: 17, 3: 18, 5: 22}
    signal read at 24 but really lives at 22

Fixed table, bits 0..5, `(align, size)`:
`{0:(8,8), 1:(1,1), 2:(1,1), 3:(2,4), 4:(1,2), 5:(1,1)}`.

With that corrected, one 5-minute capture of the whole ch36 radio:

    # AP beacons at this laptop : min=-58 max=-49 mean=-54.7 dBm  n=21790
    # phone TX at this laptop   : min=-36 max=-36 mean=-36.0 dBm  n=2

**The phone transmits 19 dB louder than the AP's own beacons arrive.** Its
radio is not weak, not silent, and not failing to key up.

## What this rules out

- **"The monitor vif cannot report RSSI."** It can, on this mt76 laptop,
  for every received frame. 21790 beacons carry a signal byte.
  [[monitor-mode-alongside-managed-captures-no-rx]] is about ath10k's vif
  receiving nothing at all; it does not generalise to the signal field, and
  it does not apply to the sniffing laptop.
- **"The phone's signal cannot be measured without the controller."** A third
  receiver measures both ends. It does not give the AP's own RSSI reading,
  but it does give the phone's transmit strength against a reference in the
  same air at the same second.
- **"The phone is too quiet to be admitted."** At the sniffer it is the
  strongest thing in the capture. Any remaining signal theory has to explain
  a transmitter that loud, and must be stated as a path-loss argument about
  the AP's position, not about the phone's radio being broken.
- **"Set the regulatory domain and the phone will transmit harder."** phy0's
  regd is **self-managed** (`country 99`, from the firmware). `iw reg set IT`
  moves the *global* regd and phy0 does not follow; `txpower` stays 30.00 dBm
  before and after. There is no regulatory lever here.
- **"The sibling WLANs also answer status=18."** They do not. They answer
  nothing -- see below.

## The AP is one radio carrying seven WLANs, and only ours is refused

Nobody had scanned the neighbourhood of `<ap-ch36>`. That BSSID is one
of **seven** on a single ch36 radio, consecutive BSSIDs `...xx:xx:xx`:

| BSSID | SSID | auth | station count |
|---|---|---|---|
| `24:...` | FG | PSK | 0 |
| `2a:...` | GUEST-SSID | PSK | 0 |
| `2e:...` | FGD1X | 802.1X | 5 |
| `32:...` | FGIOT | PSK | 0 |
| `36:...` | FGD1S | PSK | 0 |
| `3a:...` | **TEST-SSID** | PSK | 1 |
| `3e:...` | TEST-SSID-2 | PSK | 8 |

All seven beacons are identical where it could matter: capability `0x1511`,
basic rates `6/12/24`, same channel utilisation, same admission capacity.
**This is the control every earlier sweep lacked** -- distance, channel,
signal, our regdomain and our frame are all held constant by construction,
and only the WLAN policy varies.

**Every WLAN on the radio refuses us the same way, and it took an ordering
control to see it.** Probed in sequence after TEST-SSID, the siblings looked
like a *different* behaviour -- auth sent, never answered:

    TEST-SSID    auth status=0 -> assoc -> ASSOC-REJECT status_code=18
    TEST-SSID-2  auth sent -> no authentication response          <-- ARTEFACT
    GUEST-SSID       auth sent -> no authentication response          <-- ARTEFACT

That reading was wrong, and it is the trap this section exists to flag. Both
silences were exclusion that the TEST-SSID refusal had already triggered.
Take a **fresh locally-administered MAC** (no reboot needed -- `ip link set
wlan0 address`, which resets any MAC-keyed exclusion) and probe a **sibling
first, cold**, before that identity has touched TEST-SSID:

    fresh MAC: 02:c7:1c:4d:c0:7b
    GUEST-SSID     auth -> assoc -> CTRL-EVENT-ASSOC-REJECT status_code=18
    TEST-SSID  auth sent -> no response (now excluded, as expected)

**GUEST-SSID answers `status=18` too.** A different SSID, a different WLAN, a
brand-new MAC, first contact. The refusal is not TEST-SSID's policy and not
this MAC's history: it is uniform across all seven WLANs of the radio.

## Two traps that void a sweep here, both hit this session

1. **An unscoped kernel witness reprints one stale line.** `dmesg | grep
   'RX AssocResp' | tail -1` gave an identical `status=18 aid=1994` for all
   eight attempts of a sweep -- it was one historical line, re-read eight
   times. Snapshot `dmesg | wc -l` before the attempt and read only past it.
   And on this device `dmesg` carries no `RX AssocResp` at all; the journal
   does ([[dmesg-can-be-empty-about-boot]]).
2. **Probe order manufactures results.** Whatever is tried first gets the
   only real answer; everything after it is silent and looks like a distinct
   per-WLAN behaviour. Rotating within one sweep does not fix this, because
   the exclusion is already armed. Only a fresh MAC plus a cold first attempt
   does. This one nearly shipped a wrong conclusion in this very note.
3. **Back-to-back attempts test nothing after the first.** Attempt 1 gets a
   real answer; 2 onward are silent, at 0 s and at 80 s of spacing alike.
   Within a single `wpa_supplicant` run the cause is visible and is *ours*:
   `skip - BSSID ignored (count=2 limit=0)` -- the supplicant's own ignore
   list, not AP exclusion. Across separate runs the process is fresh and the
   list cannot carry over, so that silence is the AP's.

## How it was established

One capture of the whole `...xx:xx:xx` radio on a fixed mt76 monitor vif
while the phone walked TEST-SSID, TEST-SSID-2, GUEST-SSID, TEST-SSID with
dummy PSKs and 60 s gaps, plus a raw `wpa_supplicant -d` run per BSS printing
decision points rather than a classification. The radiotap fix carries an
assert-based self-check (`tools/wifi-mgmt-capture.py --demo`) that pins the signal byte to
offset 22 for a known present-mask.

**What would overturn it:** a capture where beacons carry a signal byte and
the phone's own frames do not -- that would mean the -36 dBm reading is an
artefact of which frames the vif annotates rather than a real measurement.

## The admin question, now much sharper than before

[[ap-refuses-us-our-assocreq-is-clean]] ended by asking whether that one
WLAN had Minimum RSSI, Minimum Data Rate Control, a MAC filter or a blocked
client. Two of those four are now dead: a per-WLAN MAC filter and a per-WLAN
blocked-client entry cannot refuse a **brand-new MAC on a different SSID**.
What is left has to be a policy that applies to the whole radio or the site:

> All seven WLANs on this AP's 5 GHz radio answer `status=18`
> (`ASSOC_DENIED_UNSUPP_RATE`) to a client that carries every rate in the
> advertised BSSBasicRateSet (6/12/24) -- on a first cold attempt, with a
> freshly randomised MAC, on an SSID it has never touched. A laptop that
> hears the same radio 12 dB stronger is accepted on the same second.
> Is there a **Minimum RSSI** or a site-wide **Minimum Data Rate Control**
> on that radio?

The one client-side quantity that still correlates is path loss, and it is
measured rather than assumed: the phone hears this radio at **-66..-68 dBm**
where the accepted laptop hears it at **-54.7 dBm**. That ~12 dB is the only
surviving difference between a client that is admitted and one that is not.
It does NOT mean the phone's radio is weak -- at the sniffer the phone is the
loudest thing in the capture (above). It means the phone sits further into
the path loss, which is exactly what a signal-threshold admission policy
keys on, and exactly what would make acceptance intermittent.

## What is still open, and it is no longer about our frame

`board_id 0xff` with `board_file api 2 bmi_id N/A crc32 00000000`, alongside
`invalid MAC address; choosing random` ([[taimen-has-no-factory-wlan-mac]]).
Whether a WCN3990 that matched no board-id entry is also running an
uncalibrated PA is **not established here** -- the -36 dBm reading argues
against a badly weak transmitter, and no measurement in this session
separates "uncalibrated" from "fine". Do not treat it as the cause; treat it
as the next thing to verify, and verify it with a number.
